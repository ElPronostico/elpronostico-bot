import os
import json
import logging
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

app = Flask(__name__)

BOT_TOKEN        = os.getenv("BOT_TOKEN")
CHANNEL_ID       = os.getenv("CHANNEL_ID")
ADMIN_CHAT_ID    = os.getenv("ADMIN_CHAT_ID")   # tu ID personal de Telegram (opcional)
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
EMAIL_FROM       = os.getenv("EMAIL_FROM", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    """Envía el enlace de invitación al email del comprador vía SendGrid."""
    if not SENDGRID_API_KEY or not EMAIL_FROM:
        log.warning("SendGrid no configurado — enlace para %s: %s", to_email, invite_link)
        return

    plain = f"""Hola {buyer_name},

¡Gracias por unirte a El Pronóstico VIP!

Tu acceso exclusivo al canal de Telegram está listo. Usa el enlace de abajo para unirte ahora:

  {invite_link}

IMPORTANTE: Este enlace es personal y de un solo uso. No lo compartas.

Dentro del canal encontrarás:
  • Pronósticos diarios con análisis detallado
  • Picks seleccionados con alto valor
  • Actualizaciones en tiempo real

Si tienes cualquier problema para acceder, responde a este email y te ayudamos de inmediato.

¡Mucha suerte y bienvenido/a al equipo!

El Pronóstico
"""

    html = f"""
<html>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:0;background:#f5f5f5">
  <div style="background:#0088cc;padding:30px 20px;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:26px">El Pronóstico</h1>
    <p style="color:#cce8ff;margin:6px 0 0">Canal VIP de Pronósticos</p>
  </div>

  <div style="background:#fff;padding:32px 28px">
    <h2 style="color:#0088cc;margin-top:0">¡Bienvenido/a, {buyer_name}! 🏆</h2>

    <p style="font-size:15px;color:#333;line-height:1.6">
      Gracias por confiar en <strong>El Pronóstico</strong>. Tu acceso VIP está
      activado y listo para usar. Haz clic en el botón para unirte al canal
      de Telegram ahora mismo:
    </p>

    <p style="text-align:center;margin:32px 0">
      <a href="{invite_link}"
         style="background:#0088cc;color:#fff;padding:16px 36px;
                text-decoration:none;border-radius:8px;font-size:17px;
                font-weight:bold;display:inline-block">
        Unirse al Canal VIP →
      </a>
    </p>

    <hr style="border:none;border-top:1px solid #eee;margin:28px 0">

    <p style="font-size:14px;color:#555;line-height:1.7">
      Dentro del canal encontrarás:
    </p>
    <ul style="font-size:14px;color:#333;line-height:2">
      <li>Pronósticos diarios con análisis detallado</li>
      <li>Picks seleccionados con alto valor</li>
      <li>Actualizaciones en tiempo real</li>
    </ul>

    <p style="font-size:13px;color:#e05c00;background:#fff5ee;
              padding:12px 16px;border-radius:6px;border-left:4px solid #e05c00">
      ⚠️ <strong>Enlace de un solo uso.</strong> Es personal e intransferible.
      No lo compartas con nadie.
    </p>

    <p style="font-size:14px;color:#555;margin-top:24px">
      ¿Problemas para acceder? Responde a este email y te ayudamos enseguida.
    </p>

    <p style="font-size:15px;color:#333;margin-top:28px">
      ¡Mucha suerte!<br>
      <strong style="color:#0088cc">El Pronóstico</strong>
    </p>
  </div>

  <div style="background:#f0f0f0;padding:16px;text-align:center">
    <p style="font-size:12px;color:#999;margin:0">
      Has recibido este email porque realizaste una compra en El Pronóstico.
    </p>
  </div>
</body>
</html>
"""

    message = Mail(
        from_email=(EMAIL_FROM, "El Pronóstico"),
        to_emails=to_email,
        subject="¡Bienvenido/a al Canal VIP de El Pronóstico! 🏆",
        plain_text_content=plain,
        html_content=html,
    )

    sg = SendGridAPIClient(SENDGRID_API_KEY)
    response = sg.send(message)
    log.info("Email enviado a %s — status %s", to_email, response.status_code)


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

PRODUCT_KEY = "EOirT"


@app.route("/webhook/payhip", methods=["POST"])
def payhip_webhook():
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"error": "JSON inválido"}), 400

    log.info("Webhook recibido: %s", data)

    # 1. Verificar tipo de evento
    if data.get("type") != "paid":
        log.info("Evento ignorado: %s", data.get("type"))
        return jsonify({"status": "ignored"}), 200

    # 2. Verificar que el producto correcto está en el pedido
    items = data.get("items") or []
    if not any(item.get("product_key") == PRODUCT_KEY for item in items):
        log.warning("Product key '%s' no encontrado en items: %s", PRODUCT_KEY, items)
        return jsonify({"error": "Producto no reconocido"}), 400

    # 3. Extraer datos del comprador
    buyer_email  = data.get("email", "")
    buyer_name   = data.get("name") or data.get("buyer_name", "Cliente")
    product_name = data.get("product_name", "El Pronóstico VIP")
    amount       = data.get("amount", "")
    currency     = data.get("currency", "")

    if not buyer_email:
        log.error("No se recibió email en el payload")
        return jsonify({"error": "email requerido"}), 400

    try:
        # 4. Crear enlace VIP y enviar al comprador
        invite_link = create_invite_link()
        log.info("Enlace creado para %s: %s", buyer_email, invite_link)
        send_email(buyer_email, buyer_name, invite_link)

        # 5. Notificar al admin por Telegram
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
