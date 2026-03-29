"""
Discord Content Bot — Grupo Powing
Detecta preguntas en Discord y responde con contenido del Classroom.
"""

import os
import re
import discord
import anthropic
from pathlib import Path

# ─── Config ───────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = os.getenv("MODEL", "claude-haiku-4-5-20251001")

# Canales donde el bot responde (dejar vacío = todos los canales)
# Formato: IDs separados por coma, ej: "123456789,987654321"
ALLOWED_CHANNELS = os.getenv("ALLOWED_CHANNELS", "")

# ─── Cargar base de conocimiento ──────────────────────────
knowledge_path = Path(__file__).parent / "knowledge.txt"
KNOWLEDGE = knowledge_path.read_text(encoding="utf-8")

# ─── System prompt ────────────────────────────────────────
SYSTEM_PROMPT = f"""Eres el asistente de contenido de Grupo Powing, una comunidad de agentes de seguros en México y Latinoamérica.
Tu trabajo es ayudar a los miembros a encontrar clases, módulos y recursos del Classroom en Skool.

IDENTIDAD Y TONO:
- Eres un coach amigable pero profesional y directo. No adornas, no rellenas.
- Hablas en español mexicano. Tuteas siempre (tú, no usted).
- No usas emojis. Nunca.
- Máximo 2-3 oraciones por respuesta. Si puedes en 1, mejor.

REGLAS DE CONTENIDO:
1. Responde con el nombre exacto de la clase, la sección, y el módulo al que pertenece.
2. Siempre incluye el link al módulo. Los links están como "URL:" en la base de conocimiento.
3. Si el tema aparece en más de una clase, menciona todas las relevantes.
4. Si no encuentras el tema exacto, dilo y sugiere la clase más cercana que sí exista.
5. NUNCA inventes clases, módulos o URLs que no estén en la base de conocimiento.

FUERA DE TEMA:
- Si la pregunta no es sobre contenido del Classroom o herramientas del portal, responde brevemente que eso no está en tu base de conocimiento y sugiere el contenido más relacionado que sí tengas.

SALUDOS:
- Si alguien saluda o agradece, responde con una línea breve y cálida. Sin exagerar.

BASE DE CONOCIMIENTO:
{KNOWLEDGE}"""

# ─── Detección de consultas ───────────────────────────────

# Palabras clave que indican una pregunta sobre contenido
CONTENT_KEYWORDS = [
    "clase", "clases", "módulo", "modulo", "módulos", "modulos",
    "lección", "leccion", "video", "videos", "curso", "cursos",
    "tema", "temas", "dónde", "donde", "cómo", "como",
    "classroom", "herramienta", "herramientas", "recurso", "recursos",
    "crm", "generador", "fathom", "método", "metodo", "3x",
    "objeciones", "referidos", "guión", "guion", "agendamiento",
    "agendar", "ventas", "cierre", "presentación", "presentacion",
    "linkedin", "contenido", "redes", "posts", "prospección",
    "prospeccion", "cartera", "cita", "reunión", "reunion",
    "sesión", "sesion", "vivo", "grabación", "grabacion",
    "calculadora", "ia", "inteligencia artificial",
    "calendario", "portal", "app",
]

# Patrones que indican una pregunta
QUESTION_PATTERNS = [
    r"\?",                          # Tiene signo de pregunta
    r"^(dónde|donde|cómo|como|qué|que|cuál|cual|hay|tienen|existe)",
    r"(tenés|tenes|tienes) (la |el |una |un )",
    r"(busco|necesito|quiero ver|quiero aprender)",
    r"(me (pasás|pasas|das|compartís|compartis))",
    r"(en qué|en que) (clase|módulo|modulo|video)",
]


def is_content_query(text: str) -> bool:
    """Detecta si un mensaje es una consulta sobre contenido del Classroom."""
    lower = text.lower().strip()

    # Ignorar mensajes muy cortos (saludos, reacciones)
    if len(lower) < 8:
        return False

    # Ignorar URLs sueltas, solo emojis, o solo archivos
    if lower.startswith("http") or all(c in "😀😂🤣❤️👍🔥💯✅" for c in lower.replace(" ", "")):
        return False

    # Verificar si tiene palabras clave de contenido
    has_keyword = any(kw in lower for kw in CONTENT_KEYWORDS)

    # Verificar si parece una pregunta
    has_question_pattern = any(re.search(p, lower) for p in QUESTION_PATTERNS)

    # Es consulta si: tiene keyword + pregunta, o tiene signo de ? + keyword
    return has_keyword and has_question_pattern


# ─── Cliente de IA ────────────────────────────────────────
ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def ask_ai(question: str) -> str:
    """Envía la pregunta a Claude y retorna la respuesta."""
    try:
        response = ai.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}],
        )
        return response.content[0].text
    except anthropic.APIError as e:
        print(f"[ERROR] API de Anthropic: {e}")
        return "Disculpa, tuve un problema técnico. Intenta de nuevo en unos segundos."
    except Exception as e:
        print(f"[ERROR] Inesperado: {e}")
        return "Algo salió mal. Intenta de nuevo."


# ─── Bot de Discord ───────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

# Parse de canales permitidos
allowed_channel_ids = set()
if ALLOWED_CHANNELS:
    allowed_channel_ids = {int(ch.strip()) for ch in ALLOWED_CHANNELS.split(",") if ch.strip()}


@client.event
async def on_ready():
    print(f"[BOT] Conectado como {client.user} (ID: {client.user.id})")
    print(f"[BOT] Servidores: {[g.name for g in client.guilds]}")
    if allowed_channel_ids:
        print(f"[BOT] Canales permitidos: {allowed_channel_ids}")
    else:
        print("[BOT] Respondiendo en TODOS los canales")
    print("[BOT] Listo para recibir consultas.")


@client.event
async def on_message(message: discord.Message):
    # No responder a bots (incluyéndose a sí mismo)
    if message.author.bot:
        return

    # Verificar si el canal está permitido
    if allowed_channel_ids and message.channel.id not in allowed_channel_ids:
        return

    # Verificar si es una consulta de contenido
    if not is_content_query(message.content):
        return

    # Indicador de "escribiendo..."
    async with message.channel.typing():
        answer = ask_ai(message.content)

    # Responder como reply al mensaje original
    await message.reply(answer, mention_author=False)
    print(f"[QUERY] {message.author}: {message.content[:80]}...")


# ─── Arrancar ─────────────────────────────────────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise ValueError("Falta la variable de entorno DISCORD_TOKEN")
    if not ANTHROPIC_API_KEY:
        raise ValueError("Falta la variable de entorno ANTHROPIC_API_KEY")

    client.run(DISCORD_TOKEN)
