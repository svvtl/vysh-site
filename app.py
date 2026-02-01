from flask import Flask, render_template, session, redirect, url_for, jsonify, request
import requests
import hmac
import hashlib
import json
import uuid
import base64
from datetime import datetime

app = Flask(__name__)
app.secret_key = "mysecretkey"

# ===== MONO CONFIG =====
MONO_BASE_URL = "https://u2-demo-ext.mono.st4g3.com/api/order/create"
MONO_STORE_ID = "test_store_with_confirm"
MONO_SECRET_KEY = "secret_98765432--123-123"

MONO_INVOICE_URL = "https://api.monobank.ua/api/merchant/invoice/create"

# встав сюди свій тестовий токен з кабінету
MONO_TOKEN = "uNyhCGkCeKPMh30TCFwMsfnWKdOe0bEFFh3qEELIAPL0"

# опційно – ім’я CMS (можеш написати що хочеш)
MONO_CMS_NAME = "VYSH-shop"
MONO_CMS_VERSION = "0.1"

PAID_ORDERS = set()


def mono_make_signature(body: dict) -> str:
    payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    secret_bytes = MONO_SECRET_KEY.encode("utf-8")
    hash_bytes = hmac.new(secret_bytes, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(hash_bytes).decode("utf-8")


# ===== DATA =====
ITEMS = [
    {
        "id": 1,
        "name": "Брюки",
        "price": 1899,
        "image": "pants.png",
        # все возможные размеры
        "sizes_all": ["XS", "S", "M", "L"],
        # что реально есть в наличии
        "sizes_in_stock": ["S", "M"]
    },
    {
        "id": 2,
        "name": "Годинник",
        "price": 3299,
        "image": "watch.png"
    },
    {
        "id": 3,
        "name": "Сумка",
        "price": 4599,
        "image": "bag.png"
    },
    {
        "id": 4,
        "name": "Сережки",
        "price": 1999,
        "image": "earrings.png"
    },
    {
        "id": 5,
        "name": "Туфли",
        "price": 3799,
        "image": "shoes.png",
        "sizes_all": ["36", "37", "38", "39", "40"],
        "sizes_in_stock": ["37", "38", "39"]
    },
    {
        "id": 6,
        "name": "Очки",
        "price": 2499,
        "image": "glasses.png"
    },
]


def build_mono_invoice_payload(order_id: str, total_uah: int, cart_items, customer_email: str | None):
    """
    order_id      – наш внутрішній номер замовлення (типу ORDER-XXXX)
    total_uah     – сума в гривнях (899), не в копійках
    cart_items    – список елементів з checkout (як у тебе вже є)
    customer_email – може бути None або пустий рядок
    """

    basket = []
    for ci in cart_items:
        item = ci["item"]
        qty = ci["qty"]
        item_total = ci["total"]

        basket.append({
            "name": item["name"],                 # назва товару
            "qty": qty,                           # кількість
            "sum": int(item["price"] * 100),      # ціна за 1 в копійках
            "total": int(item_total * 100),       # сума за позицію в копійках
            "code": str(item["id"]),              # артикул / id
            "unit": "шт."
        })

    payload = {
        "amount": int(total_uah * 100),  # вся сума в копійках
        "ccy": 980,                      # гривня
        "merchantPaymInfo": {
            "reference": order_id,                       # наш номер замовлення
            "destination": "Оплата замовлення VYSH",    # що оплачуємо
            "comment": f"Замовлення {order_id}",
        },
        "customerEmails": [customer_email] if customer_email else [],
        "basketOrder": basket,

        # Куди повертати клієнта (тут URL нашого сайту)
        "redirectUrl": url_for("order_success", _external=True),
        "successUrl": url_for("order_success", _external=True),
        "failureUrl": url_for("checkout", _external=True),

        # Куди monobank шле callback зі статусом
        "webHookUrl": url_for("mono_webhook", _external=True),

        # скільки живе рахунок – 24 години
        "validity": 24 * 60 * 60,
        "paymentType": "debit",  # звичайна оплата карткою
        # інші поля типу qrId, saveCardData, tipsEmployeeId ми не чіпаємо – вони не обов'язкові
    }

    return payload


USD_RATE = 39
AVAILABLE_PARTS = [3, 6, 10]


# ===== PAGES =====
@app.route("/")
def home():
    # 1,2,3,4 → Топ продаж
    top_sales = [item for item in ITEMS if item["id"] in (1, 2, 3, 4)]

    # 5,6 → Акції
    sale_items = [item for item in ITEMS if item["id"] in (5, 6)]

    return render_template(
        "index.html",
        top_sales=top_sales,
        sale_items=sale_items
    )


@app.route("/products")
def products():
    return render_template("products.html", items=ITEMS, usd_rate=USD_RATE)


@app.route("/contact")
def contact():
    return render_template("contact.html")


# ===== CART =====
@app.context_processor
def inject_cart_count():
    cart = session.get("cart", {})
    return {"cart_count": sum(cart.values()) if cart else 0}


@app.route("/add-to-cart/<int:item_id>", methods=["POST"])
def add_to_cart(item_id):
    cart = session.get("cart", {})
    sid = str(item_id)
    cart[sid] = cart.get(sid, 0) + 1
    session["cart"] = cart
    return redirect(url_for("products"))


@app.route("/cart")
def cart():
    cart = session.get("cart", {})
    cart_items = []
    total = 0

    for item in ITEMS:
        qty = cart.get(str(item["id"]))
        if qty:
            total_item = qty * item["price"]
            total += total_item
            cart_items.append({
                "item": item,
                "qty": qty,
                "total": total_item
            })

    return render_template("cart.html", cart_items=cart_items, total=total)


# ===== CHECKOUT =====
@app.route("/checkout", methods=["GET", "POST"])
def checkout():

    current_order = session.get("store_order_id")

    # 🔥 ЯДРО ЛОГІКИ
    if current_order and current_order in PAID_ORDERS:
        session.pop("cart", None)
        session.pop("store_order_id", None)
        PAID_ORDERS.remove(current_order)

        return redirect(url_for("order_success"))

    cart = session.get("cart", {})
    cart_items = []
    total = 0

    # формуємо список товарів з кошика
    for item in ITEMS:
        qty = cart.get(str(item["id"]))
        if qty:
            item_total = qty * item["price"]
            total += item_total
            cart_items.append({
                "item": item,
                "qty": qty,
                "total": item_total,
            })

    # якщо кошик порожній – назад у каталог / кошик
    if not cart_items:
        return redirect(url_for("products"))

    # генеруємо номер замовлення, якщо його ще немає
    if "store_order_id" not in session:
        session["store_order_id"] = f"ORDER-{uuid.uuid4().hex[:10].upper()}"

    order_id = session["store_order_id"]

    if request.method == "POST":
        # 1. забираємо дані з форми
        name = request.form.get("name")
        phone = request.form.get("phone")
        email = request.form.get("email")
        address = request.form.get("address")

        # можна зберегти в сесії (на майбутнє, якщо будеш мати БД)
        session["customer_info"] = {
            "name": name,
            "phone": phone,
            "email": email,
            "address": address,
        }

        # 2. готуємо payload для monobank
        invoice_payload = build_mono_invoice_payload(
            order_id=order_id,
            total_uah=total,
            cart_items=cart_items,
            customer_email=email,
        )

        # 3. шлемо запит на створення рахунку
        headers = {
            "X-Token": MONO_TOKEN,
            "Content-Type": "application/json",
            "X-Cms": MONO_CMS_NAME,
            "X-Cms-Version": MONO_CMS_VERSION,
        }

        try:
            resp = requests.post(
                MONO_INVOICE_URL,
                headers=headers,
                json=invoice_payload,
                timeout=10,
            )
        except Exception as e:
            # якщо не дісталися до monobank (мережа впала / ще щось)
            return f"Помилка з’єднання з monobank: {e}", 500

        # 4. розбираємо відповідь
        try:
            data = resp.json()
        except Exception:
            data = {}

        # за докою monobank повертає посилання на оплату (часто поле називається pageUrl)
        if resp.status_code == 200 and "pageUrl" in data:
            # очищати кошик і order_id поки НЕ будемо – ми ще не знаємо, чи клієнт заплатив
            return redirect(data["pageUrl"])
        else:
            # на дебаг: покажемо, що повернув monobank
            return f"Помилка створення рахунку: {resp.status_code}, {data}", 500

    # GET – просто показати сторінку оформлення
    return render_template(
        "checkout.html",
        cart_items=cart_items,
        total=total,
        store_order_id=order_id,
    )


# ===== MONO PARTS =====
@app.route("/pay-parts", methods=["POST"])
def pay_parts():
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    parts_count = int(data.get("parts_count", 0)) 
    if not phone:
        return jsonify({
            "success": False,
            "error": "Не вказано номер телефону"
        }), 400

    cart = session.get("cart", {})
    if not cart:
        return jsonify({
            "success": False,
            "error": "Кошик порожній"
        }), 400

    total_sum = 0
    products = []

    for item in ITEMS:
        qty = cart.get(str(item["id"]))
        if qty:
            item_total = item["price"] * qty
            total_sum += item_total
            products.append({
                "name": item["name"],
                "count": qty,
                "sum": round(item_total, 2)
            })

    store_order_id = session.get("store_order_id")

    body = {
        "store_order_id": store_order_id,
        "client_phone": phone,
        "total_sum": round(total_sum, 2),
        "invoice": {
            "number": store_order_id,
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "point_id": 123,
            "source": "INTERNET"
        },
        "available_programs": [{
            "available_parts_count": [3, 6, 10],
            "selected_parts_count": parts_count,
            "type": "payment_installments"
        }],

        "products": products,
        "result_callback": "https://example.com/"
    }

    # Підпис
    payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    signature = base64.b64encode(
        hmac.new(
            MONO_SECRET_KEY.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256
        ).digest()
    ).decode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "store-id": MONO_STORE_ID,
        "signature": signature
    }

    # ==== Весь блок try — всередині функції ====
    try:
        print("PAYLOAD TO MONO:", payload)

        mono_resp = requests.post(
            MONO_BASE_URL,
            headers=headers,
            data=payload,
            timeout=10
        )

        print("MONO API STATUS:", mono_resp.status_code)
        print("MONO API RESPONSE:", mono_resp.text)

        if mono_resp.status_code in (200, 201):
            session.pop("cart", None)
            return jsonify({
                "success": True,
                "redirect_url": url_for("order_success")
            })

        print("MONO API ERROR TEXT:", mono_resp.text)
        return jsonify({
            "success": False,
            "error": "При оформленні Покупки частинами сталась помилка"
        }), 400

    except Exception as e:
        print("EXCEPTION OCCURRED:", str(e))
        return jsonify({
            "success": False,
            "error": "Сталася технічна помилка. Спробуйте пізніше."
        }), 502

# ===== SUCCESS =====
@app.route("/order-success")
def order_success():
    session.pop("cart", None)
    session.pop("store_order_id", None)
    return render_template("order_success.html")


@app.route("/decrease/<int:item_id>", methods=["POST"])
def decrease(item_id):
    cart = session.get("cart", {})
    sid = str(item_id)
    if sid in cart:
        if cart[sid] > 1:
            cart[sid] -= 1
        else:
            del cart[sid]
    session["cart"] = cart
    return redirect(url_for("cart"))


@app.route("/remove-from-cart/<int:item_id>", methods=["POST"])
def remove_from_cart(item_id):
    cart = session.get("cart", {})
    sid = str(item_id)
    if sid in cart:
        del cart[sid]
    session["cart"] = cart
    return redirect(url_for("cart"))

@app.context_processor
def inject_cart_count():
    cart = session.get("cart", {})
    return {"cart_count": sum(cart.values()) if cart else 0}

@app.route("/api/add-to-cart/<int:item_id>", methods=["POST"])
def api_add_to_cart(item_id):
    cart = session.get("cart", {})
    sid = str(item_id)
    cart[sid] = cart.get(sid, 0) + 1
    session["cart"] = cart
    return jsonify({"success": True})

@app.route("/cart-preview")
def cart_preview():
    cart = session.get("cart", {})
    items = []
    total = 0

    for item in ITEMS:
        qty = cart.get(str(item["id"]))
        if qty:
            item_total = qty * item["price"]
            total += item_total
            items.append({
                "id": item["id"],
                "name": item["name"],
                "price": item["price"],
                "image": item["image"],
                "qty": qty
            })

    return jsonify({
        "items": items,
        "total": total
    })
@app.route("/cart/increase/<int:item_id>", methods=["POST"])
def cart_increase(item_id):
    cart = session.get("cart", {})
    sid = str(item_id)
    cart[sid] = cart.get(sid, 0) + 1
    session["cart"] = cart
    return jsonify({"success": True})


@app.route("/cart/decrease/<int:item_id>", methods=["POST"])
def cart_decrease(item_id):
    cart = session.get("cart", {})
    sid = str(item_id)
    if sid in cart:
        if cart[sid] > 1:
            cart[sid] -= 1
        else:
            del cart[sid]
    session["cart"] = cart
    return jsonify({"success": True})

@app.route("/payment/success")
def payment_success():
    # TODO: тут можна показати "дякуємо, оплата пройшла"
    return render_template("payment_success.html")


@app.route("/payment/fail")
def payment_fail():
    # TODO: сторінка, якщо клієнт скасував оплату / сталася помилка
    return render_template("payment_fail.html")


@app.route("/payment/return")
def payment_return():
    # monobank може використати redirectUrl
    # поки просто перекинемо на success
    return redirect(url_for("payment_success"))



@app.route("/mono_webhook", methods=["POST"])
def mono_webhook():
    data = request.json
    print("MONO WEBHOOK:", data)

    status = data.get("status")
    reference = data.get("reference")  # ORDER-XXXX

    if status == "success" and reference:
        PAID_ORDERS.add(reference)
        print("PAID_ORDERS:", PAID_ORDERS)

    return "ok"

@app.route("/toggle-favorite/<int:item_id>", methods=["POST"])
def toggle_favorite(item_id):
    favorites = session.get("favorites", [])

    if item_id in favorites:
        favorites.remove(item_id)
        is_fav = False
    else:
        favorites.append(item_id)
        is_fav = True

    session["favorites"] = favorites
    session.modified = True

    return jsonify({"active": is_fav})


if __name__ == "__main__":
    app.run(debug=True)
