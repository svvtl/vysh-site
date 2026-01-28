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

MONO_BASE_URL = "https://u2-demo-ext.mono.st4g3.com/api/order/create"  # проверь точный путь в доке
MONO_STORE_ID = "test_store_with_confirm"
MONO_SECRET_KEY = "secret_98765432--123-123"

def mono_make_signature(body: dict) -> str:
    payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    secret_bytes = MONO_SECRET_KEY.encode("utf-8")
    hash_bytes = hmac.new(secret_bytes, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(hash_bytes).decode("utf-8")

# Временные данные товаров
ITEMS = [
    {
        "id": 1,
        "name": "Футболка",
        "price": 399,
        "description": "Зручна футболка з логотипом",
        "image": "tshirt1.png",
    },
    {
        "id": 2,
        "name": "Рюкзак",
        "price": 899,
        "description": "Вмістка торба першокласника",
        "image": "backpack1.png",
    },
    {
        "id": 3,
        "name": "Годинник",
        "price": 1299,
        "description": "Не ролекс, але згодиться",
        "image": "watch1.png",
    },
]

USD_RATE = 39


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/products")
def products():
    return render_template("products.html", items=ITEMS, usd_rate=USD_RATE)


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/product/<int:item_id>")
def product(item_id):
    item = next((item for item in ITEMS if item["id"] == item_id), None)
    if item is None:
        return "Товар не знайдено", 404
    return render_template("product.html", item=item)


# ➕ Добавить товар в корзину
@app.route("/add-to-cart/<int:item_id>", methods=["POST"])
def add_to_cart(item_id):
    cart = session.get("cart", {})
    item_id = str(item_id)

    cart[item_id] = cart.get(item_id, 0) + 1
    session["cart"] = cart

    return redirect(url_for("products"))


# 🛒 Корзина
@app.route("/cart")
def cart():
    cart = session.get("cart", {})
    cart_items = []
    total = 0

    for item in ITEMS:
        qty = cart.get(str(item["id"]))
        if qty:
            item_total = qty * item["price"]
            total += item_total
            cart_items.append({
                "item": item,
                "qty": qty,
                "total": item_total
            })

    return render_template("cart.html", cart_items=cart_items, total=total)


# ❌ Удалить товар полностью
@app.route("/remove-from-cart/<int:item_id>", methods=["POST"])
def remove_from_cart(item_id):
    cart = session.get("cart", {})
    str_id = str(item_id)

    if str_id in cart:
        del cart[str_id]

    session["cart"] = cart
    return redirect(url_for("cart"))
@app.route("/decrease/<int:item_id>", methods=["POST"])
def decrease(item_id):
    cart = session.get("cart", {})
    str_id = str(item_id)

    if str_id in cart:
        cart[str_id] -= 1
        if cart[str_id] <= 0:
            del cart[str_id]

    session["cart"] = cart
    return redirect(url_for("cart"))
@app.context_processor
def inject_cart_count():
    """
    Делаем переменную cart_count доступной во ВСЕХ шаблонах.
    """
    cart = session.get("cart", {})
    # суммируем количество всех товаров
    count = sum(cart.values()) if cart else 0
    return {"cart_count": count}


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
                "image": item["image"],
                "price": item["price"],
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

    if sid in cart:
        cart[sid] += 1
        session["cart"] = cart

    return jsonify(success=True)


@app.route("/cart/decrease/<int:item_id>", methods=["POST"])
def cart_decrease(item_id):
    cart = session.get("cart", {})
    sid = str(item_id)

    if sid in cart:
        cart[sid] -= 1
        if cart[sid] <= 0:
            del cart[sid]
        session["cart"] = cart

    return jsonify(success=True)

@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart = session.get("cart", {})
    cart_items = []
    total = 0

    for item in ITEMS:
        qty = cart.get(str(item["id"]))
        if qty:
            item_total = qty * item["price"]
            total += item_total
            cart_items.append({
                "item": item,
                "qty": qty,
                "total": item_total
            })

    # ✅ генерація номера замовлення, якщо ще немає
    if 'store_order_id' not in session:
        session['store_order_id'] = f"ORDER-{uuid.uuid4().hex[:10].upper()}"

    # ✅ POST — підтвердження форми
    if request.method == "POST":
        session.pop("cart", None)  # очищаємо лише кошик, НЕ чіпаємо store_order_id
        return redirect(url_for("order_success"))

    # ✅ GET — показуємо сторінку
    return render_template(
        'checkout.html',
        cart_items=cart_items,
        total=total,
        store_order_id=session['store_order_id']
    )

@app.route("/pay-parts", methods=["POST"])
def pay_parts():
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()

    if not phone:
        return jsonify({"status": "error", "message": "Не вказано номер телефону"}), 400

    cart = session.get("cart", {})
    if not cart:
        return jsonify({"status": "error", "message": "Кошик порожній"}), 400

    total_sum = 0
    products = []

    for item in ITEMS:
        qty = cart.get(str(item["id"]))
        if not qty:
            continue

        item_total = item["price"] * qty
        total_sum += item_total
        products.append({
            "name": item["name"],
            "count": qty,
            "sum": round(item_total, 2)
        })

    store_order_id = f"ORDER-{uuid.uuid4().hex[:10]}"

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
            "available_parts_count": [10],
            "type": "payment_installments"
        }],
        "products": products,
        "result_callback": "https://example.com/mono-callback"
    }

    payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    signature = mono_make_signature(body)

    headers = {
        "Content-Type": "application/json",
        "store-id": MONO_STORE_ID,
        "signature": signature
    }

    try:
        mono_resp = requests.post(
            MONO_BASE_URL,
            headers=headers,
            data=payload,
            timeout=10
        )

        if mono_resp.status_code == 201:
            session.pop("cart", None)
            return jsonify({"status": "success"})

        try:
            err_json = mono_resp.json()
        except ValueError:
            err_json = {"raw": mono_resp.text}

        return jsonify({
            "status": "error",
            "message": "Mono повернув помилку",
            "mono_status": mono_resp.status_code,
            "mono_body": err_json
        }), 400

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Помилка запиту до mono: {e}"
        }), 502


@app.route("/order-success")
def order_success():
    store_order_id = session.get("store_order_id", "НЕВІДОМИЙ")
    
    # показуємо сторінку
    response = render_template("order_success.html", store_order_id=store_order_id)

    # ✅ лише тепер видаляємо номер замовлення
    session.pop("store_order_id", None)

    return response

@app.route("/cart/remove/<int:item_id>", methods=["POST"])
def cart_remove(item_id):
    cart = session.get("cart", {})
    sid = str(item_id)

    if sid in cart:
        del cart[sid]
        session["cart"] = cart

    return jsonify(success=True)


if __name__ == "__main__":
    app.run(debug=True)

