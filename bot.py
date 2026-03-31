"""
Discord Content Bot — Grupo Powing
Detecta preguntas en Discord y responde con contenido del Classroom.
"""

import os
import re
import time
import asyncio
import hashlib
import discord
import anthropic
from pathlib import Path

# ─── Config ───────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = os.getenv("MODEL", "claude-haiku-4-5-20251001")

ALLOWED_CHANNELS = os.getenv("ALLOWED_CHANNELS", "")
USER_COOLDOWN = int(os.getenv("USER_COOLDOWN", "60"))
CHANNEL_COOLDOWN = int(os.getenv("CHANNEL_COOLDOWN", "30"))
DEBOUNCE_SECONDS = int(os.getenv("DEBOUNCE_SECONDS", "3"))

# ─── Cargar base de conocimiento ──────────────────────────
knowledge_path = Path(__file__).parent / "knowledge.txt"
KNOWLEDGE = knowledge_path.read_text(encoding="utf-8")

# ─── System prompt ────────────────────────────────────────
SYSTEM_PROMPT = f"""Eres el asistente de contenido de Grupo Powing (agentes de seguros en LATAM).
Ayudas a encontrar clases y recursos del Classroom en Skool.

TONO: Español mexicano, tuteo, directo, sin emojis. Máximo 2-3 oraciones.

REGLAS:
1. Incluye links como hipervínculo: [Nombre](URL). NUNCA URL crudo.
2. NO uses nombres internos de secciones (como "LI — BOT"). Describe natural.
3. Si hay varios videos, lista numerada:
   1. [Clase 1](URL1)
   2. [Clase 2](URL2)
4. Si no encuentras el tema, dilo y sugiere lo más cercano.
5. NUNCA inventes clases o URLs.
6. Si el mensaje NO es una pregunta clara sobre contenido del Classroom, responde SOLO: SKIP

BASE DE CONOCIMIENTO:
{KNOWLEDGE}"""

# ─── Detección de consultas ───────────────────────────────

# STRONG = palabras que casi siempre indican búsqueda de contenido
STRONG_KEYWORDS = [
    r"\bclase\b", r"\bclases\b", r"\bmódulo\b", r"\bmodulo\b",
    r"\blección\b", r"\bleccion\b", r"\bclassroom\b",
    r"\bcurso\b", r"\bcursos\b",
    r"\bfathom\b", r"\b3x\b", r"\bmétodo 3x\b",
    r"\bsales navigator\b",
    r"\bgenerador\b", r"\bcalculadora\b",
    r"\bguión\b", r"\bguion\b",
]

# WEAK = podrían indicar búsqueda, pero también aparecen en conversación normal
WEAK_KEYWORDS = [
    r"\bvideo\b", r"\bvideos\b",
    r"\bherramienta\b", r"\bherramientas\b",
    r"\bcrm\b", r"\bobjeciones\b", r"\breferidos\b",
    r"\bagendamiento\b", r"\bagendar\b", r"\bcierre\b",
    r"\bpresentación\b", r"\bpresentacion\b",
    r"\blinkedin\b", r"\bposts\b",
    r"\bprospección\b", r"\bprospeccion\b",
    r"\bportal\b", r"\bbot\b",
    r"\binstalo\b", r"\binstalar\b", r"\binstalación\b",
    r"\binteligencia artificial\b",
    r"\bwhatsapp empresarial\b",
    r"\bformulario\b", r"\bagenda\b",
    r"\bnicho\b", r"\bperfil\b",
]

QUESTION_PATTERNS = [
    r"\?",
    r"^(dónde|donde|cómo|como|qué|que|cuál|cual)\b.{5,}",
    r"\b(busco|necesito|quiero ver|quiero aprender)\b",
    r"\b(me (pasás|pasas|das|compartís|compartis|envías|envias|mandas))\b",
    r"\b(en qué|en que) (clase|módulo|modulo|video)\b",
    r"\b(hay (una |alguna )?(clase|lección|video|módulo))\b",
    r"\b(dónde|donde) (está|esta|encuentro|veo|consigo)\b",
    r"\b(cómo|como) (instalo|configuro|hago|uso|activo|armo|creo)\b",
]

# Frases que indican conversación casual, meta, o sobre el bot
META_PATTERNS = [
    r"asistente de contenido",
    r"\bbot\b.{0,20}(ruido|molest|spam|error|problema|respond|desconect|crash)",
    r"(ruido|molest|spam).{0,20}\bbot\b",
    r"se (borr|elimin|desaparec)",
    r"me respond(e|ió|en) (en )?automátic",
    r"está(s)? (generando|haciendo|causando)",
    r"qué (te |les? )?parece",
    r"qué (dices|opinan|opinas|crees)",
    r"\b(pendientes|tareas|sprint|daily|standup)\b",
    r"\b(hablé|hable|hablamos|platicamos|llam[eé]|contacté)\b",
    r"\bte (escribo|mando|envío|contacto)\b",
    r"\b(felicidades|felicitaciones|bien hecho|excelente trabajo)\b",
    r"\b(reunión|reunion|junta|llamada) (de |del |con )(equipo|trabajo|lunes|martes|miércoles|jueves|viernes)\b",
]


def is_content_query(text: str) -> bool:
    """Detecta si un mensaje es una consulta sobre contenido del Classroom."""
    lower = text.lower().strip()

    # Ignorar mensajes cortos
    if len(lower) < 12:
        return False

    # Ignorar URLs sueltas
    if lower.startswith("http"):
        return False

    # Ignorar mensajes META (sobre el bot, quejas, conversación casual)
    if any(re.search(p, lower) for p in META_PATTERNS):
        return False

    # Contar keywords por nivel
    strong_count = sum(1 for kw in STRONG_KEYWORDS if re.search(kw, lower))
    weak_count = sum(1 for kw in WEAK_KEYWORDS if re.search(kw, lower))

    has_question = any(re.search(p, lower) for p in QUESTION_PATTERNS)

    # 1+ strong + pregunta → SÍ
    # 2+ weak + pregunta → SÍ
    # 1 weak + pregunta → NO (demasiado ambiguo)
    if strong_count >= 1 and has_question:
        return True
    if weak_count >= 2 and has_question:
        return True

    return False


# ─── Cache de respuestas ─────────────────────────────────
response_cache = {}
CACHE_TTL = 3600


def cache_key(text: str) -> str:
    normalized = re.sub(r"[^a-záéíóúñü0-9 ]", "", text.lower().strip())
    normalized = re.sub(r"\s+", " ", normalized)
    return hashlib.md5(normalized.encode()).hexdigest()


def get_cached(text: str) -> str | None:
    key = cache_key(text)
    if key in response_cache:
        answer, ts = response_cache[key]
        if time.time() - ts < CACHE_TTL:
            print(f"[CACHE] Hit: {text[:50]}...")
            return answer
        else:
            del response_cache[key]
    return None


def set_cache(text: str, answer: str):
    response_cache[cache_key(text)] = (answer, time.time())


# ─── Cliente de IA ────────────────────────────────────────
ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def ask_ai(question: str) -> str | None:
    """Envía la pregunta a Claude. Retorna None si falla o no aplica."""
    cached = get_cached(question)
    if cached:
        return cached

    try:
        response = ai.messages.create(
            model=MODEL,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}],
        )
        answer = response.content[0].text.strip()

        if answer.upper() == "SKIP":
            return None

        set_cache(question, answer)
        return answer
    except anthropic.APIError as e:
        print(f"[ERROR] API: {e}")
        return None
    except Exception as e:
        print(f"[ERROR] Inesperado: {e}")
        return None


# ─── Bot de Discord ───────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

allowed_channel_ids = set()
if ALLOWED_CHANNELS:
    allowed_channel_ids = {int(ch.strip()) for ch in ALLOWED_CHANNELS.split(",") if ch.strip()}

# Cooldown trackers
user_last_reply = {}
channel_last_reply = {}

# Debounce tracker: {user_id: asyncio.Task}
pending_tasks = {}


@client.event
async def on_ready():
    print(f"[BOT] Conectado como {client.user} (ID: {client.user.id})")
    print(f"[BOT] Servidores: {[g.name for g in client.guilds]}")
    if allowed_channel_ids:
        print(f"[BOT] Canales: {allowed_channel_ids}")
    else:
        print("[BOT] Respondiendo en TODOS los canales")
    print(f"[BOT] Cooldown usuario: {USER_COOLDOWN}s | canal: {CHANNEL_COOLDOWN}s | debounce: {DEBOUNCE_SECONDS}s")
    print("[BOT] Listo.")


async def process_message(message: discord.Message):
    """Procesa el mensaje después del debounce."""
    await asyncio.sleep(DEBOUNCE_SECONDS)

    # Re-verificar cooldowns (pudieron cambiar durante el debounce)
    now = time.time()
    if now - user_last_reply.get(message.author.id, 0) < USER_COOLDOWN:
        return
    if now - channel_last_reply.get(message.channel.id, 0) < CHANNEL_COOLDOWN:
        return

    async with message.channel.typing():
        answer = ask_ai(message.content)

    if answer is None:
        return

    await message.reply(answer, mention_author=False, suppress_embeds=True)

    now = time.time()
    user_last_reply[message.author.id] = now
    channel_last_reply[message.channel.id] = now
    print(f"[QUERY] {message.author}: {message.content[:80]}...")


@client.event
async def on_message(message: discord.Message):
    # No responder a bots
    if message.author.bot:
        return

    # No responder a admins
    if message.author.guild_permissions.administrator:
        return

    # Verificar canal permitido
    if allowed_channel_ids and message.channel.id not in allowed_channel_ids:
        return

    # Ignorar replies a otros humanos (es conversación entre ellos)
    if message.reference and message.reference.message_id:
        try:
            ref_msg = message.reference.cached_message
            if ref_msg is None:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
            if ref_msg.author.id != client.user.id:
                return
        except Exception:
            return

    # Cooldowns
    now = time.time()
    if now - user_last_reply.get(message.author.id, 0) < USER_COOLDOWN:
        return
    if now - channel_last_reply.get(message.channel.id, 0) < CHANNEL_COOLDOWN:
        return

    # Verificar si es consulta de contenido (filtra saludos, gracias, conversación)
    if not is_content_query(message.content):
        return

    # Debounce: cancelar tarea anterior del mismo usuario
    user_id = message.author.id
    if user_id in pending_tasks:
        pending_tasks[user_id].cancel()

    pending_tasks[user_id] = asyncio.create_task(process_message(message))


# ─── Arrancar ─────────────────────────────────────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise ValueError("Falta la variable de entorno DISCORD_TOKEN")
    if not ANTHROPIC_API_KEY:
        raise ValueError("Falta la variable de entorno ANTHROPIC_API_KEY")

    client.run(DISCORD_TOKEN)
