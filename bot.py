import os
import json
import asyncio
import logging
from typing import Any

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gab-storm-bot")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CONFIG_PATH = os.getenv("PRODUCTS_CONFIG", "products.json")
OWNER_IDS = {int(x.strip()) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()}
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0") or 0)
RESET_LOG_CHANNEL_ID = int(os.getenv("RESET_LOG_CHANNEL_ID", "0") or 0)
CLIENT_RESET_CHANNEL_ID = int(os.getenv("CLIENT_RESET_CHANNEL_ID", "0") or 0)
CLIENT_RESET_ROLE_ID = int(os.getenv("CLIENT_RESET_ROLE_ID", "0") or 0)

def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        raise RuntimeError("products.json precisa conter uma lista 'products'.")
    return data

CONFIG = load_config()
PRODUCTS = {
    item["name"]: item
    for item in CONFIG["products"]
    if isinstance(item, dict) and item.get("name")
}
RESELLERS: set[int] = set()

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix=".", intents=intents)

def seller_key(product: dict[str, Any]) -> str:
    # Prefer environment variable; optionally permit a literal key in local config.
    env_name = product.get("seller_key_env", "")
    return os.getenv(env_name, "").strip() if env_name else str(product.get("seller_key", "")).strip()

def product_choices():
    return [
        app_commands.Choice(name=name[:100], value=name)
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
        app_commands.Choice(name=str(n), value=n)
        for n in (1, 5, 10, 15, 25, 50, 100)
    ]

async def keyauth_request(params: dict[str, str]) -> tuple[str, Any]:
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get("https://keyauth.win/api/seller/", params=params) as response:
            text = await response.text()
            response.raise_for_status()
            try:
                return text, json.loads(text)
            except json.JSONDecodeError:
                return text, None

async def send_log(embed: discord.Embed, channel_id: int | None = None):
    target_id = channel_id or LOG_CHANNEL_ID
    if not target_id:
        return
    channel = bot.get_channel(target_id)
    if channel:
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Falha ao enviar log para canal %s", target_id)

def is_staff(user_id: int) -> bool:
    return user_id in OWNER_IDS or user_id in RESELLERS

def product_embed(title: str, description: str) -> discord.Embed:
    color = int(CONFIG.get("embed_color", "0x5865F2"), 0)
    embed = discord.Embed(title=title, description=description, color=color)
    brand = CONFIG.get("brand_name", "Gab Storm")
    embed.set_footer(text=brand)
    logo = CONFIG.get("logo_url", "")
    if logo:
        embed.set_thumbnail(url=logo)
    return embed

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        log.info("Conectado como %s; %s comandos sincronizados", bot.user, len(synced))
    except Exception:
        log.exception("Falha ao sincronizar comandos")

@bot.tree.command(name="generate", description="Gera licenças para um produto cadastrado")
@app_commands.choices(product=product_choices(), duration=duration_choices(), amount=amount_choices())
async def generate(interaction: discord.Interaction, product: str, duration: str, amount: int, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not is_staff(interaction.user.id):
        await interaction.followup.send("Você não tem permissão para gerar licenças.", ephemeral=True)
        return
    config = PRODUCTS.get(product)
    key = seller_key(config) if config else ""
    if not key:
        await interaction.followup.send(f"Produto **{product}** sem chave de vendedor configurada.", ephemeral=True)
        return
    try:
        text, parsed = await keyauth_request({
            "sellerkey": key, "type": "add", "expiry": duration,
            "mask": config.get("mask", "***-***-***"), "level": str(config.get("level", 1)),
            "amount": str(amount), "format": "text"
        })
        if parsed and isinstance(parsed, dict) and parsed.get("success") is False:
            raise RuntimeError(parsed.get("message", "KeyAuth recusou a solicitação"))
        license_key = text.strip().upper()
        if not license_key:
            raise RuntimeError("A API não retornou uma licença.")
    except Exception as exc:
        log.exception("Erro ao gerar licença")
        await interaction.followup.send(f"Erro ao gerar licença: {exc}", ephemeral=True)
        return

    embed = product_embed(
        f"{CONFIG.get('brand_name', 'Gab Storm')} | Licença gerada",
        f"Licença para **{product}**:\n```{license_key}```\n"
        f"**Produto:** {product}\n**Duração:** {duration} dia(s) (9999 = vitalício)"
    )
    try:
        await user.send(embed=embed)
        delivery = f"Licença enviada por DM para {user.mention}."
    except discord.Forbidden:
        delivery = f"Não foi possível enviar DM para {user.mention}; licença exibida aqui."
    await interaction.followup.send(embed=embed, ephemeral=True)
    await send_log(product_embed("Registro de geração",
        f"**Produto:** {product}\n**Licença:** `{license_key}`\n"
        f"**Entregue a:** {user.mention}\n**Gerado por:** {interaction.user.mention}\n{delivery}"))

@bot.tree.command(name="reset-key", description="Reseta o HWID de uma licença")
@app_commands.choices(product=product_choices())
async def reset_key(interaction: discord.Interaction, key: str, product: str):
    await interaction.response.defer(ephemeral=True)
    if not is_staff(interaction.user.id):
        await interaction.followup.send("Acesso negado.", ephemeral=True)
        return
    config = PRODUCTS.get(product)
    sk = seller_key(config) if config else ""
    if not sk:
        await interaction.followup.send(f"Produto **{product}** sem chave de vendedor configurada.", ephemeral=True)
        return
    try:
        _, result = await keyauth_request({"sellerkey": sk, "type": "resetuser", "user": key})
        message = result.get("message", "Reset de HWID solicitado.") if isinstance(result, dict) else "Reset de HWID solicitado."
    except Exception as exc:
        log.exception("Erro no reset de HWID")
        await interaction.followup.send(f"Erro ao resetar HWID: {exc}", ephemeral=True)
        return
    await interaction.followup.send(embed=product_embed("Reset de HWID", f"Produto: **{product}**\nLicença: `{key}`\n{message}"), ephemeral=True)
    await send_log(product_embed("Log de reset de HWID", f"**Produto:** {product}\n**Licença:** `{key}`\n**Executado por:** {interaction.user.mention}"), RESET_LOG_CHANNEL_ID)

@bot.tree.command(name="delete-key", description="Exclui uma licença")
@app_commands.choices(product=product_choices())
async def delete_key(interaction: discord.Interaction, key: str, product: str):
    await interaction.response.defer(ephemeral=True)
    if not is_staff(interaction.user.id):
        await interaction.followup.send("Acesso negado.", ephemeral=True)
        return
    config = PRODUCTS.get(product)
    sk = seller_key(config) if config else ""
    if not sk:
        await interaction.followup.send(f"Produto **{product}** sem chave de vendedor configurada.", ephemeral=True)
        return
    try:
        _, result = await keyauth_request({"sellerkey": sk, "type": "del", "key": key})
        message = result.get("message", "Solicitação de exclusão concluída.") if isinstance(result, dict) else "Solicitação de exclusão concluída."
    except Exception as exc:
        log.exception("Erro ao excluir licença")
        await interaction.followup.send(f"Erro ao excluir licença: {exc}", ephemeral=True)
        return
    await interaction.followup.send(embed=product_embed("Licença excluída", f"Produto: **{product}**\nLicença: `{key}`\n{message}"), ephemeral=True)
    await send_log(product_embed("Log de exclusão", f"**Produto:** {product}\n**Licença:** `{key}`\n**Executado por:** {interaction.user.mention}"), RESET_LOG_CHANNEL_ID)

@bot.tree.command(name="add-panel", description="Adiciona um revendedor")
async def add_panel(interaction: discord.Interaction, user: discord.User):
    if interaction.user.id not in OWNER_IDS:
        await interaction.response.send_message("Somente proprietários podem adicionar revendedores.", ephemeral=True)
        return
    RESELLERS.add(user.id)
    await interaction.response.send_message(embed=product_embed("Revendedor adicionado", f"{user.mention} recebeu acesso aos comandos."), ephemeral=True)
    await send_log(product_embed("Revendedor adicionado", f"**Revendedor:** {user.mention}\n**Por:** {interaction.user.mention}"))

@bot.tree.command(name="remove-panel", description="Remove um revendedor")
async def remove_panel(interaction: discord.Interaction, user: discord.User):
    if interaction.user.id not in OWNER_IDS:
        await interaction.response.send_message("Somente proprietários podem remover revendedores.", ephemeral=True)
        return
    if user.id not in RESELLERS:
        await interaction.response.send_message(f"{user.mention} não está na lista de revendedores.", ephemeral=True)
        return
    RESELLERS.remove(user.id)
    await interaction.response.send_message(embed=product_embed("Revendedor removido", f"{user.mention} perdeu acesso aos comandos."), ephemeral=True)
    await send_log(product_embed("Revendedor removido", f"**Revendedor:** {user.mention}\n**Por:** {interaction.user.mention}"))

@bot.tree.command(name="client-reset", description="Solicita reset de HWID para cliente autorizado")
@app_commands.choices(product=product_choices())
async def client_reset(interaction: discord.Interaction, key: str, product: str):
    await interaction.response.defer(ephemeral=True)
    if CLIENT_RESET_CHANNEL_ID and interaction.channel_id != CLIENT_RESET_CHANNEL_ID:
        await interaction.followup.send(f"Use este comando no canal <#{CLIENT_RESET_CHANNEL_ID}>.", ephemeral=True)
        return
    if CLIENT_RESET_ROLE_ID:
        roles = getattr(interaction.user, "roles", [])
        if not any(role.id == CLIENT_RESET_ROLE_ID for role in roles):
            await interaction.followup.send("Você não possui o cargo necessário.", ephemeral=True)
            return
    config = PRODUCTS.get(product)
    sk = seller_key(config) if config else ""
    if not sk:
        await interaction.followup.send(f"Produto **{product}** sem chave de vendedor configurada.", ephemeral=True)
        return
    try:
        _, result = await keyauth_request({"sellerkey": sk, "type": "resetuser", "user": key})
        message = result.get("message", "Solicitação de reset enviada.") if isinstance(result, dict) else "Solicitação de reset enviada."
    except Exception as exc:
        log.exception("Erro no client-reset")
        await interaction.followup.send(f"Erro ao solicitar reset: {exc}", ephemeral=True)
        return
    await interaction.followup.send(embed=product_embed("Solicitação de reset", f"Produto: **{product}**\n{message}"), ephemeral=True)
    await send_log(product_embed("Solicitação de reset do cliente", f"**Produto:** {product}\n**Licença:** `{key}`\n**Cliente:** {interaction.user.mention}"))

if not TOKEN:
    raise RuntimeError("Defina a variável de ambiente DISCORD_TOKEN.")
bot.run(TOKEN)
