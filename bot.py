import os
import json
import sqlite3
import logging
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands


# =========================================================
# LOGS
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

log = logging.getLogger("gab-storm-bot")


# =========================================================
# CONFIGURAÇÕES
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

CONFIG_PATH = os.getenv("PRODUCTS_CONFIG", "products.json")


def get_int_env(name: str, default: int = 0) -> int:
    value = os.getenv(name, str(default)).strip()

    try:
        return int(value) if value else default
    except ValueError:
        log.warning("Variável %s possui um valor inválido.", name)
        return default


OWNER_IDS = {
    int(x.strip())
    for x in os.getenv(
        "OWNER_IDS",
        "1205345667271102477"
    ).split(",")
    if x.strip().isdigit()
}

LOG_CHANNEL_ID = get_int_env(
    "LOG_CHANNEL_ID",
    1566512212523229425
)

RESET_LOG_CHANNEL_ID = get_int_env(
    "RESET_LOG_CHANNEL_ID",
    1566512212523229425
)

CLIENT_RESET_CHANNEL_ID = get_int_env(
    "CLIENT_RESET_CHANNEL_ID",
    1566512592158195732
)

CLIENT_RESET_ROLE_ID = get_int_env(
    "CLIENT_RESET_ROLE_ID",
    1563611112690159690
)


# =========================================================
# BANCO DE DADOS DOS REVENDEDORES
# =========================================================

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(
    os.getenv(
        "RESELLERS_DB",
        str(DATA_DIR / "resellers.db")
    )
)

DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_database():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS resellers (
                user_id INTEGER PRIMARY KEY
            )
        """)

    log.info("Banco de dados dos revendedores inicializado.")


def load_resellers() -> set[int]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT user_id FROM resellers"
        ).fetchall()

    return {int(row[0]) for row in rows}


def add_reseller(user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO resellers (user_id) VALUES (?)",
            (user_id,)
        )


def remove_reseller(user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM resellers WHERE user_id = ?",
            (user_id,)
        )


init_database()
RESELLERS: set[int] = load_resellers()


# =========================================================
# CONFIGURAÇÃO DOS PRODUTOS
# =========================================================

def load_config() -> dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

    except FileNotFoundError:
        raise RuntimeError(
            f"Arquivo {CONFIG_PATH} não encontrado."
        )

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Erro no JSON de produtos: {exc}"
        )

    if not isinstance(data, dict):
        raise RuntimeError(
            "O arquivo de produtos precisa ser um objeto JSON."
        )

    if not isinstance(data.get("products"), list):
        raise RuntimeError(
            "products.json precisa conter uma lista 'products'."
        )

    return data


CONFIG = load_config()

PRODUCTS = {
    item["name"]: item
    for item in CONFIG["products"]
    if isinstance(item, dict) and item.get("name")
}


if not PRODUCTS:
    raise RuntimeError(
        "Nenhum produto válido foi encontrado no products.json."
    )


# =========================================================
# DISCORD
# =========================================================

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(
    command_prefix=".",
    intents=intents
)


# =========================================================
# INICIALIZAÇÃO E SINCRONIZAÇÃO
# =========================================================

class GabStormBot(commands.Bot):

    async def setup_hook(self):
        try:
            synced = await self.tree.sync()

            log.info(
                "%s comandos sincronizados com o Discord.",
                len(synced)
            )

        except discord.HTTPException as exc:
            log.error(
                "Falha ao sincronizar comandos. HTTP %s.",
                exc.status
            )

        except Exception as exc:
            log.error(
                "Erro na sincronização: %s",
                type(exc).__name__
            )


bot = GabStormBot(
    command_prefix=".",
    intents=intents
)


@bot.event
async def on_ready():
    log.info("======================================")
    log.info("GAB STORM BOT ONLINE")
    log.info("Conectado como: %s", bot.user)
    log.info("Revendedores cadastrados: %s", len(RESELLERS))
    log.info("Produtos carregados: %s", len(PRODUCTS))
    log.info("======================================")


# =========================================================
# KEYAUTH
# =========================================================

def seller_key(product: dict[str, Any]) -> str:
    env_name = product.get("seller_key_env", "")

    if env_name:
        return os.getenv(env_name, "").strip()

    return str(
        product.get("seller_key", "")
    ).strip()


async def keyauth_request(
    params: dict[str, str]
) -> tuple[str, Any]:

    timeout = aiohttp.ClientTimeout(total=25)

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            "https://keyauth.win/api/seller/",
            params=params
        ) as response:

            response_text = await response.text()

            if response.status >= 400:
                raise RuntimeError(
                    f"A KeyAuth retornou HTTP {response.status}."
                )

            try:
                parsed = json.loads(response_text)

            except json.JSONDecodeError:
                parsed = None

            return response_text, parsed


def api_error_message(exc: Exception) -> str:

    if isinstance(exc, asyncio.TimeoutError):
        return "A KeyAuth demorou demais para responder. Tente novamente."

    if isinstance(exc, aiohttp.ClientError):
        return "Não foi possível estabelecer comunicação com a KeyAuth."

    return str(exc)[:300]


# =========================================================
# OPÇÕES DOS COMANDOS
# =========================================================

def product_choices():

    return [
        app_commands.Choice(
            name=name[:100],
            value=name
        )
        for name in PRODUCTS
    ][:25]


def duration_choices():

    return [
        app_commands.Choice(name="1 dia", value="1"),
        app_commands.Choice(name="7 dias", value="7"),
        app_commands.Choice(name="30 dias", value="30"),
        app_commands.Choice(name="Vitalício", value="9999"),
    ]


def amount_choices():

    return [
        app_commands.Choice(
            name=str(number),
            value=number
        )
        for number in (1, 5, 10, 15, 25, 50, 100)
    ]


# =========================================================
# PERMISSÕES
# =========================================================

def is_staff(user_id: int) -> bool:

    return (
        user_id in OWNER_IDS
        or user_id in RESELLERS
    )


# =========================================================
# EMBEDS
# =========================================================

def product_embed(
    title: str,
    description: str
) -> discord.Embed:

    try:
        color = int(
            str(CONFIG.get("embed_color", "0x5865F2")),
            0
        )

    except ValueError:
        color = 0x5865F2

    embed = discord.Embed(
        title=title,
        description=description,
        color=color
    )

    brand = CONFIG.get(
        "brand_name",
        "Gab Storm"
    )

    embed.set_footer(text=brand)

    logo = CONFIG.get("logo_url", "")

    if logo:
        embed.set_thumbnail(url=logo)

    return embed


# =========================================================
# SISTEMA DE LOGS
# =========================================================

async def send_log(
    embed: discord.Embed,
    channel_id: int | None = None
):

    target_id = channel_id or LOG_CHANNEL_ID

    if not target_id:
        return

    try:
        channel = bot.get_channel(target_id)

        if channel is None:
            channel = await bot.fetch_channel(target_id)

        await channel.send(embed=embed)

    except discord.Forbidden:
        log.warning(
            "Sem permissão para enviar logs no canal %s.",
            target_id
        )

    except discord.NotFound:
        log.warning(
            "Canal de logs %s não encontrado.",
            target_id
        )

    except discord.HTTPException as exc:
        log.warning(
            "Falha ao enviar log. HTTP %s.",
            exc.status
        )


# =========================================================
# COMANDO: GENERATE
# =========================================================

@bot.tree.command(
    name="generate",
    description="Gera licenças para um produto cadastrado"
)
@app_commands.choices(
    product=product_choices(),
    duration=duration_choices(),
    amount=amount_choices()
)
async def generate(
    interaction: discord.Interaction,
    product: str,
    duration: str,
    amount: int,
    user: discord.Member
):

    await interaction.response.defer(ephemeral=True)

    if not is_staff(interaction.user.id):
        await interaction.followup.send(
            "Você não tem permissão para gerar licenças.",
            ephemeral=True
        )
        return

    config = PRODUCTS.get(product)
    key = seller_key(config) if config else ""

    if not key:
        await interaction.followup.send(
            f"Produto **{product}** sem chave de vendedor configurada.",
            ephemeral=True
        )
        return

    try:
        text, parsed = await keyauth_request({
            "sellerkey": key,
            "type": "add",
            "expiry": duration,
            "mask": config.get("mask", "***-***-***"),
            "level": str(config.get("level", 1)),
            "amount": str(amount),
            "format": "text"
        })

        if (
            isinstance(parsed, dict)
            and parsed.get("success") is False
        ):
            raise RuntimeError(
                parsed.get(
                    "message",
                    "KeyAuth recusou a solicitação."
                )
            )

        license_key = text.strip().upper()

        if not license_key:
            raise RuntimeError(
                "A API não retornou uma licença."
            )

    except Exception as exc:
        log.error(
            "Erro ao gerar licença: %s",
            type(exc).__name__
        )

        await interaction.followup.send(
            f"Erro ao gerar licença: {api_error_message(exc)}",
            ephemeral=True
        )
        return

    embed = product_embed(
        f"{CONFIG.get('brand_name', 'Gab Storm')} | Licença gerada",
        (
            f"Licença para **{product}**:\n"
            f"```{license_key}```\n"
            f"**Produto:** {product}\n"
            f"**Duração:** {duration} dia(s) "
            f"(9999 = vitalício)"
        )
    )

    try:
        await user.send(embed=embed)

        delivery = (
            f"Licença enviada por DM para {user.mention}."
        )

    except discord.Forbidden:
        delivery = (
            f"Não foi possível enviar DM para {user.mention}; "
            "licença exibida apenas na resposta privada."
        )

    await interaction.followup.send(
        embed=embed,
        ephemeral=True
    )

    await send_log(
        product_embed(
            "Registro de geração",
            (
                f"**Produto:** {product}\n"
                f"**Licença:** `{license_key}`\n"
                f"**Entregue a:** {user.mention}\n"
                f"**Gerado por:** {interaction.user.mention}\n"
                f"{delivery}"
            )
        )
    )


# =========================================================
# COMANDO: RESET-KEY
# =========================================================

@bot.tree.command(
    name="reset-key",
    description="Reseta o HWID de uma licença"
)
@app_commands.choices(product=product_choices())
async def reset_key(
    interaction: discord.Interaction,
    key: str,
    product: str
):

    await interaction.response.defer(ephemeral=True)

    if not is_staff(interaction.user.id):
        await interaction.followup.send(
            "Acesso negado.",
            ephemeral=True
        )
        return

    config = PRODUCTS.get(product)
    sk = seller_key(config) if config else ""

    if not sk:
        await interaction.followup.send(
            f"Produto **{product}** sem chave de vendedor configurada.",
            ephemeral=True
        )
        return

    try:
        _, result = await keyauth_request({
            "sellerkey": sk,
            "type": "resetuser",
            "user": key
        })

        message = (
            result.get("message", "Reset de HWID solicitado.")
            if isinstance(result, dict)
            else "Reset de HWID solicitado."
        )

    except Exception as exc:
        log.error(
            "Erro no reset de HWID: %s",
            type(exc).__name__
        )

        await interaction.followup.send(
            f"Erro ao resetar HWID: {api_error_message(exc)}",
            ephemeral=True
        )
        return

    await interaction.followup.send(
        embed=product_embed(
            "Reset de HWID",
            (
                f"Produto: **{product}**\n"
                f"Licença: `{key}`\n"
                f"{message}"
            )
        ),
        ephemeral=True
    )

    await send_log(
        product_embed(
            "Log de reset de HWID",
            (
                f"**Produto:** {product}\n"
                f"**Licença:** `{key}`\n"
                f"**Executado por:** {interaction.user.mention}"
            )
        ),
        RESET_LOG_CHANNEL_ID
    )


# =========================================================
# COMANDO: DELETE-KEY
# =========================================================

@bot.tree.command(
    name="delete-key",
    description="Exclui uma licença"
)
@app_commands.choices(product=product_choices())
async def delete_key(
    interaction: discord.Interaction,
    key: str,
    product: str
):

    await interaction.response.defer(ephemeral=True)

    if not is_staff(interaction.user.id):
        await interaction.followup.send(
            "Acesso negado.",
            ephemeral=True
        )
        return

    config = PRODUCTS.get(product)
    sk = seller_key(config) if config else ""

    if not sk:
        await interaction.followup.send(
            f"Produto **{product}** sem chave de vendedor configurada.",
            ephemeral=True
        )
        return

    try:
        _, result = await keyauth_request({
            "sellerkey": sk,
            "type": "del",
            "key": key
        })

        message = (
            result.get(
                "message",
                "Solicitação de exclusão concluída."
            )
            if isinstance(result, dict)
            else "Solicitação de exclusão concluída."
        )

    except Exception as exc:
        log.error(
            "Erro ao excluir licença: %s",
            type(exc).__name__
        )

        await interaction.followup.send(
            f"Erro ao excluir licença: {api_error_message(exc)}",
            ephemeral=True
        )
        return

    await interaction.followup.send(
        embed=product_embed(
            "Licença excluída",
            (
                f"Produto: **{product}**\n"
                f"Licença: `{key}`\n"
                f"{message}"
            )
        ),
        ephemeral=True
    )

    await send_log(
        product_embed(
            "Log de exclusão",
            (
                f"**Produto:** {product}\n"
                f"**Licença:** `{key}`\n"
                f"**Executado por:** {interaction.user.mention}"
            )
        ),
        RESET_LOG_CHANNEL_ID
    )


# =========================================================
# COMANDO: ADD-PANEL
# =========================================================

@bot.tree.command(
    name="add-panel",
    description="Adiciona um revendedor"
)
async def add_panel(
    interaction: discord.Interaction,
    user: discord.User
):

    if interaction.user.id not in OWNER_IDS:
        await interaction.response.send_message(
            "Somente proprietários podem adicionar revendedores.",
            ephemeral=True
        )
        return

    if user.id in RESELLERS:
        await interaction.response.send_message(
            f"{user.mention} já está cadastrado como revendedor.",
            ephemeral=True
        )
        return

    try:
        add_reseller(user.id)
        RESELLERS.add(user.id)

    except sqlite3.Error:
        log.exception("Falha ao salvar revendedor no banco.")

        await interaction.response.send_message(
            "Não foi possível salvar o revendedor no banco de dados.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        embed=product_embed(
            "Revendedor adicionado",
            f"{user.mention} recebeu acesso aos comandos."
        ),
        ephemeral=True
    )

    await send_log(
        product_embed(
            "Revendedor adicionado",
            (
                f"**Revendedor:** {user.mention}\n"
                f"**Por:** {interaction.user.mention}"
            )
        )
    )


# =========================================================
# COMANDO: REMOVE-PANEL
# =========================================================

@bot.tree.command(
    name="remove-panel",
    description="Remove um revendedor"
)
async def remove_panel(
    interaction: discord.Interaction,
    user: discord.User
):

    if interaction.user.id not in OWNER_IDS:
        await interaction.response.send_message(
            "Somente proprietários podem remover revendedores.",
            ephemeral=True
        )
        return

    if user.id not in RESELLERS:
        await interaction.response.send_message(
            f"{user.mention} não está na lista de revendedores.",
            ephemeral=True
        )
        return

    try:
        remove_reseller(user.id)
        RESELLERS.discard(user.id)

    except sqlite3.Error:
        log.exception("Falha ao remover revendedor do banco.")

        await interaction.response.send_message(
            "Não foi possível remover o revendedor do banco de dados.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        embed=product_embed(
            "Revendedor removido",
            f"{user.mention} perdeu acesso aos comandos."
        ),
        ephemeral=True
    )

    await send_log(
        product_embed(
            "Revendedor removido",
            (
                f"**Revendedor:** {user.mention}\n"
                f"**Por:** {interaction.user.mention}"
            )
        )
    )


# =========================================================
# COMANDO: CLIENT-RESET
# =========================================================

@bot.tree.command(
    name="client-reset",
    description="Solicita reset de HWID para cliente autorizado"
)
@app_commands.choices(product=product_choices())
async def client_reset(
    interaction: discord.Interaction,
    key: str,
    product: str
):

    await interaction.response.defer(ephemeral=True)

    if (
        CLIENT_RESET_CHANNEL_ID
        and interaction.channel_id != CLIENT_RESET_CHANNEL_ID
    ):
        await interaction.followup.send(
            f"Use este comando no canal <#{CLIENT_RESET_CHANNEL_ID}>.",
            ephemeral=True
        )
        return

    if CLIENT_RESET_ROLE_ID:
        roles = getattr(interaction.user, "roles", [])

        if not any(
            role.id == CLIENT_RESET_ROLE_ID
            for role in roles
        ):
            await interaction.followup.send(
                "Você não possui o cargo necessário.",
                ephemeral=True
            )
            return

    config = PRODUCTS.get(product)
    sk = seller_key(config) if config else ""

    if not sk:
        await interaction.followup.send(
            f"Produto **{product}** sem chave de vendedor configurada.",
            ephemeral=True
        )
        return

    try:
        _, result = await keyauth_request({
            "sellerkey": sk,
            "type": "resetuser",
            "user": key
        })

        message = (
            result.get(
                "message",
                "Solicitação de reset enviada."
            )
            if isinstance(result, dict)
            else "Solicitação de reset enviada."
        )

    except Exception as exc:
        log.error(
            "Erro no client-reset: %s",
            type(exc).__name__
        )

        await interaction.followup.send(
            f"Erro ao solicitar reset: {api_error_message(exc)}",
            ephemeral=True
        )
        return

    await interaction.followup.send(
        embed=product_embed(
            "Solicitação de reset",
            (
                f"Produto: **{product}**\n"
                f"{message}"
            )
        ),
        ephemeral=True
    )

    await send_log(
        product_embed(
            "Solicitação de reset do cliente",
            (
                f"**Produto:** {product}\n"
                f"**Licença:** `{key}`\n"
                f"**Cliente:** {interaction.user.mention}"
            )
        )
    )


# =========================================================
# INICIAR BOT
# =========================================================

if not TOKEN:
    raise RuntimeError(
        "Defina a variável de ambiente DISCORD_TOKEN no Render."
    )


if __name__ == "__main__":
    bot.run(TOKEN)
