"""
Overwatch Twitch Drop Farmer
Usa o Chromium do Playwright com perfil local (sessão salva após primeiro login).

Requisitos:
    pip install playwright
    python -m playwright install chromium

Na primeira execução: faça login na Twitch manualmente no navegador que abrir.
Nas próximas execuções: já entra logado automaticamente.
"""

import asyncio
import sys
import random
import logging
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

# ─── Configuração de log ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("farm_log.txt", encoding="utf-8"),
    ],
)
log = logging.getLogger("OWFarm")

# ─── Canais alvo (ordem de prioridade) ───────────────────────────────────────
CHANNELS = [
    "ow_esports",
    "ow_esports_jp",
]

# ─── Perfil local do Chromium (salva sessão entre execuções) ─────────────────
CHROMIUM_PROFILE = str(Path.home() / "AppData" / "Local" / "ow_farm_profile")

# ─── Intervalos ──────────────────────────────────────────────────────────────
CHECK_INTERVAL_SEC  = 60   # checagem de status e claim na stream
INVENTORY_CHECK_MIN = 2    # checagem do inventário a cada N minutos
OFFLINE_WAIT_MIN    = 5    # espera quando todos os canais estão offline

DROPS_INVENTORY_URL = "https://www.twitch.tv/drops/inventory?lang=en"


def channel_url(name: str) -> str:
    return f"https://www.twitch.tv/{name}"


async def random_delay(min_s: float = 1.0, max_s: float = 3.0):
    await asyncio.sleep(random.uniform(min_s, max_s))


# ─── Seletor único de claim ───────────────────────────────────────────────────
CLAIM_SELECTOR = (
    '[data-test-selector="drops-claim-button"], '
    '[data-a-target="drops-claim-button"]'
)


# ─── Tenta dar claim direto na página do stream ───────────────────────────────
async def try_claim_on_page(page) -> int:
    claimed = 0
    try:
        buttons = page.locator(CLAIM_SELECTOR)
        count = await buttons.count()
        for i in range(count):
            try:
                await buttons.nth(i).click()
                claimed += 1
                log.info(f"🎁 Drop resgatado direto na stream! ({claimed})")
                await random_delay(0.5, 1.0)
            except Exception:
                pass
    except Exception:
        pass
    return claimed


# ─── Claim no inventário (fallback a cada 2 minutos) ─────────────────────────
async def claim_from_inventory(context) -> int:
    claimed = 0
    page = await context.new_page()
    try:
        await page.goto(DROPS_INVENTORY_URL, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(4)

        buttons = page.locator(CLAIM_SELECTOR)
        count = await buttons.count()
        for i in range(count):
            try:
                await buttons.nth(i).click()
                await random_delay(1.0, 2.0)
                claimed += 1
                log.info(f"🎁 Drop {claimed} resgatado no inventário!")
            except Exception:
                pass

    except Exception as e:
        log.warning(f"⚠️  Erro ao acessar inventário: {e}")
    finally:
        await page.close()

    return claimed


# ─── Configura menor qualidade possível ──────────────────────────────────────
async def set_lowest_quality(page):
    try:
        await page.click('[data-a-target="player-settings-button"]', timeout=5_000)
        await random_delay(0.8, 1.5)
        await page.click('[data-a-target="player-settings-menu-item-quality"]', timeout=5_000)
        await random_delay(0.5, 1.0)
        options = page.locator('[data-a-target="player-settings-menu"] input[type="radio"]')
        count = await options.count()
        if count > 0:
            await options.nth(count - 1).click()
            log.info("🎛️  Qualidade ajustada para mínima.")
        await page.keyboard.press("Escape")
    except Exception:
        pass


# ─── Garante que o player está tocando ───────────────────────────────────────
async def ensure_playing(page):
    try:
        play_btn = page.locator('[data-a-target="player-play-pause-button"]')
        if await play_btn.count() > 0:
            label = await play_btn.get_attribute("aria-label") or ""
            if "Play" in label or "Reproduzir" in label:
                await play_btn.click()
                log.info("▶️  Player estava pausado, retomando...")
    except Exception:
        pass


# ─── Verifica se canal está ao vivo ──────────────────────────────────────────
async def is_live(page, channel: str) -> bool:
    try:
        await page.goto(channel_url(channel), wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(4)
        return await page.locator('[data-a-target="live-badge"]').count() > 0
    except Exception:
        return False


# ─── Assiste ao canal até ele sair do ar ─────────────────────────────────────
async def watch_channel(page, context, channel: str):
    log.info(f"▶️  Configurando player em {channel}...")
    await asyncio.sleep(3)
    await set_lowest_quality(page)
    await ensure_playing(page)

    # Claim imediato ao entrar
    await claim_from_inventory(context)

    checks = 0
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        checks += 1

        if await page.locator('[data-a-target="live-badge"]').count() == 0:
            log.warning(f"📴  {channel} saiu do ar.")
            return

        await ensure_playing(page)
        await try_claim_on_page(page)

        if checks % INVENTORY_CHECK_MIN == 0:
            await claim_from_inventory(context)

        await page.mouse.move(
            random.randint(400, 800),
            random.randint(300, 500)
        )

        mins = (checks * CHECK_INTERVAL_SEC) // 60
        log.info(f"⏱️  [{channel}] {mins} min assistidos — {datetime.now().strftime('%H:%M')}")


# ─── Login manual na primeira execução ───────────────────────────────────────
async def ensure_logged_in(page) -> bool:
    await page.goto("https://www.twitch.tv/", wait_until="domcontentloaded")
    await asyncio.sleep(3)

    if await page.locator('[data-a-target="user-menu-toggle"]').count() > 0:
        log.info("✅ Sessão da Twitch detectada!")
        return True

    log.info("🔑 Sessão não encontrada. Abrindo página de login...")
    log.info("👉 Faça login na Twitch no navegador e depois pressione ENTER aqui no cmd.")
    await page.goto("https://www.twitch.tv/login", wait_until="domcontentloaded")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, input, "")

    if await page.locator('[data-a-target="user-menu-toggle"]').count() > 0:
        log.info("✅ Login realizado com sucesso!")
        return True

    log.error("❌ Login não detectado. Encerrando.")
    return False


# ─── Loop principal ───────────────────────────────────────────────────────────
async def main():
    log.info("🚀 Iniciando Overwatch Drop Farmer (Chromium)...")
    log.info(f"📁 Perfil salvo em: {CHROMIUM_PROFILE}")

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=CHROMIUM_PROFILE,
            headless=False,
            args=[
                "--mute-audio",
                "--disable-blink-features=AutomationControlled",
            ],
            viewport={"width": 1280, "height": 720},
        )

        # Fecha abas extras e reutiliza a primeira (evita erro de contexto vazio)
        log.info("🧹 Fechando abas anteriores...")
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        for p in pages[1:]:
            await p.close()
        await page.goto("about:blank")

        # Verifica sessão (ou abre login na primeira vez)
        logged_in = await ensure_logged_in(page)
        if not logged_in:
            await context.close()
            sys.exit(1)

        log.info("🎮 Iniciando farm...")

        # Loop principal de farm
        while True:
            encontrou_live = False

            for channel in CHANNELS:
                log.info(f"🔍 Verificando {channel}...")

                if await is_live(page, channel):
                    encontrou_live = True
                    log.info(f"🟢 {channel} está AO VIVO! Farmando drops...")
                    await watch_channel(page, context, channel)
                    log.info("🔄 Canal caiu. Buscando próximo canal disponível...")
                else:
                    log.info(f"🔴 {channel} está offline.")

            if not encontrou_live:
                log.info(f"😴 Todos os canais offline. Aguardando {OFFLINE_WAIT_MIN} min...")
                await asyncio.sleep(OFFLINE_WAIT_MIN * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("⛔ Farm encerrado pelo usuário.")
