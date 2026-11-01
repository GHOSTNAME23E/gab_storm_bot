# Gab Storm Discord Bot (genérico)

Bot Discord com comandos `/generate`, `/reset-key`, `/delete-key`, `/add-panel`, `/remove-panel` e `/client-reset`.

## Configuração no Render

1. Suba estes arquivos para um repositório GitHub.
2. No Render, crie um **Blueprint** e conecte o repositório (`render.yaml`).
3. Configure as variáveis de ambiente:
   - `DISCORD_TOKEN`: token do bot Discord (não coloque no código).
   - `OWNER_IDS`: IDs dos proprietários separados por vírgula.
   - `LOG_CHANNEL_ID`: canal de logs de geração e painéis (opcional).
   - `RESET_LOG_CHANNEL_ID`: canal de logs de reset/exclusão (opcional).
   - `CLIENT_RESET_CHANNEL_ID`: canal permitido para `/client-reset` (opcional; `0` desativa).
   - `CLIENT_RESET_ROLE_ID`: cargo exigido para `/client-reset` (opcional; `0` desativa).
   - `KEYAUTH_PRODUCT_1` e `KEYAUTH_PRODUCT_2`: seller keys dos produtos de exemplo.
4. Faça deploy.

## Adicionar produtos

Edite `products.json`, adicionando objetos dentro de `products`:

```json
{
  "name": "Nome do produto",
  "seller_key_env": "NOME_DA_VARIAVEL_NO_RENDER",
  "mask": "***-***-***",
  "level": 1
}
```

Depois crie a variável de ambiente com o nome informado e a seller key correspondente no Render, e faça novo deploy. O Discord permite até 25 opções por comando slash.

## Observações importantes

- Os comandos de licenças usam a API Seller do KeyAuth. Para produtos de outro fornecedor, será necessário implementar a integração desse fornecedor.
- `/add-panel` e `/remove-panel` mantêm a lista de revendedores apenas em memória; ela reinicia vazia quando o serviço reinicia. Para persistência, é necessário adicionar armazenamento (por exemplo, SQLite ou banco de dados).
- Ative os intents necessários no Discord Developer Portal, especialmente Server Members Intent se usar cargos/checagens de membros.
- Nunca publique tokens ou seller keys no GitHub. Se os valores antigos foram expostos, revogue-os e gere novos.
