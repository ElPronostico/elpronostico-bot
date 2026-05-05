import os
import hmac
import hashlib
import json
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

app = Flask(__name__)

BOT_TOKEN     = os.getenv("BOT_TOKEN")
CHANNEL_ID    = os.getenv("CHANNEL_ID")
PAYHIP_SECRET = os.getenv("PAYHIP_SECRET")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")   # tu ID personal de Telegram (opcional)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp-mail.outlook.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def verify_signature(raw_body: bytes, signature: str) -> bool:
    """Verifica que el webhook viene realmente de PayHip."""
    expected = hmac.new(
        PAYHIP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def create_invite_link() -> str:
    """Crea un enlace de invitación de un solo uso al canal VIP."""
    resp = requests.post(
        f"{TELEGRAM_API}/createChatInviteLink",
        json={
            "chat_id": CHANNEL_ID,
            "member_limit": 1,           # un solo uso por cliente
            "creates_join_request": False,
        },
        timeout=10,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data["result"]["invite_link"]


def send_email(to_email: str, buyer_name: str, invite_link: str) -> None:
    """Envía el enlace de invitación al email del comprador."""
    if not SMTP_USER or not SMTP_PASS:
        log.warning("SMTP no configurado — enlace para %s: %s", to_email, invite_link)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Tu acceso al Canal VIP — El Pronóstico ✅"
    msg["From"]    = SMTP_USER
    msg["To"]      = to_email

    plain = f"""
Hola {buyer_name},

Gracias por tu compra. Aquí está tu enlace de acceso al Canal VIP de El Pronóstico:

{invite_link}

⚠️ Este enlace es de un solo uso. Úsalo para unirte al canal y guárdalo.

¡Bienvenido/a!
El Pronóstico
"""

    html = f"""
<html>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
  <h2 style="color:#0088cc">¡Bienvenido/a al Canal VIP!</h2>
  <p>Hola <strong>{buyer_name}</strong>,</p>
  <p>Gracias por tu compra. Haz clic en el botón para unirte al
  <strong>Canal VIP de El Pronóstico</strong>:</p>
  <p style="text-align:center;margin:30px 0">
    <a href="{invite_link}"
       style="background:#0088cc;color:#fff;padding:14px 28px;
              text-decoration:none;border-radius:8px;font-size:16px">
      Unirse al Canal VIP
    </a>
  </p>
  <p style="color:#888;font-size:13px">
    ⚠️ Este enlace es de un solo uso. Si tienes problemas, contáctanos.
  </p>
  <p>¡Buena suerte con tus pronósticos!<br>
  <strong>El Pronóstico</strong></p>
</body>
</html>
"""

    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html,  "html",  "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, to_email, msg.as_string())

    log.info("Email enviado a %s", to_email)


def notify_admin(buyer_name: str, buyer_email: str, product: str, amount: str) -> None:
    """Notifica al admin en Telegram cuando hay una compra nueva."""
    if not ADMIN_CHAT_ID:
        return
    text = (
        "✅ *Nueva compra — El Pronóstico*\n\n"
        f"👤 {buyer_name}\n"
        f"📧 {buyer_email}\n"
        f"📦 {product}\n"
        f"💰 {amount}"
    )
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/webhook/payhip", methods=["POST"])
def payhip_webhook():
    raw_body  = request.get_data()
    signature = request.headers.get("X-Payhip-Signature", "")

    if not verify_signature(raw_body, signature):
        log.warning("Firma inválida recibida")
        return jsonify({"error": "Firma inválida"}), 401

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        return jsonify({"error": "JSON inválido"}), 400

    log.info("Webhook recibido: %s", data)

    # PayHip usa el campo "type" o "event"; aceptamos ambos
    event = data.get("type") or data.get("event", "")
    if event not in ("sale", "payment_completed", ""):
        log.info("Evento ignorado: %s", event)
        return jsonify({"status": "ignored"}), 200

    buyer_email  = data.get("buyer_email", "")
    buyer_name   = data.get("buyer_name", "Cliente")
    product_name = data.get("product_name", "El Pronóstico VIP")
    amount       = data.get("amount", "")
    currency     = data.get("currency", "")

    if not buyer_email:
        log.error("No se recibió buyer_email en el payload")
        return jsonify({"error": "buyer_email requerido"}), 400

    try:
        invite_link = create_invite_link()
        log.info("Enlace creado para %s: %s", buyer_email, invite_link)

        send_email(buyer_email, buyer_name, invite_link)
        notify_admin(buyer_name, buyer_email, product_name, f"{amount} {currency}")

        return jsonify({"status": "ok", "invite_link": invite_link}), 200

    except Exception as exc:
        log.exception("Error procesando webhook: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "bot": BOT_TOKEN[:10] + "..."}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    log.info("Servidor iniciado en puerto %s", port)
    app.run(host="0.0.0.0", port=port, debug=False)
