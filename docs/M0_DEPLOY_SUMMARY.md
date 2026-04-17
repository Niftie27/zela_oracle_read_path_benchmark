# M0 Deploy — Oracle Read Path Benchmark

Tento dokument popisuje kompletní postup nasazení M0 milestone na Zela platformu.
M0 cíl: postavit minimální Zela proceduru, která čte jeden Pyth oracle account
a měří latency, aby se ověřilo, že celá pipeline funguje.

**Výsledek:** M0 nasazeno úspěšně, procedura funguje na Solana mainnet, latency ~600 µs.

---

## Před-deploy checklist

Před samotným deployem muselo být hotové:

1. **GitHub repo** `Niftie27/zela_oracle_read_path_benchmark` vytvořené
2. **Lokální kód** v `~/code/zela_oracle_read_path_benchmark/` pushnut na GitHub
3. **Zela dashboard** — projekt `zela_oracle_read_path_benchmark` a prázdná
   procedura `oracle_read` vytvořené
4. **Zela API key** vygenerovaný, Client ID a Secret uložené
5. **Lokální `.env`** soubor s credentials
6. **`.gitignore`** obsahuje `.env` aby se secrets nepushly na GitHub

---

## Setup credentials

### Vytvoření .env souboru

**Co to je:** `.env` je lokální textový soubor s tajnými hodnotami (API keys, IDs).
Neposílá se na GitHub. Shell ho umí načíst a udělat z každého řádku environment
proměnnou, kterou pak můžeme používat v `curl` příkazech jako `$PROMENNA`.

**Proč to děláme:** Abys nemusel hard-codovat secrets do každého příkazu nebo si
je pamatovat.

```bash
cd ~/code/zela_oracle_read_path_benchmark
nano .env
```

Obsah souboru (hodnoty nahrazené skutečnými z Zela dashboardu):

```
ZELA_KEY_ID=tvuj_client_id_z_dashboardu
ZELA_KEY_SECRET=tvuj_secret_z_dashboardu
ZELA_PROJECT=60cdf54f-42a6-47d8-ab19-db93d4248e50
ZELA_PROCEDURE=oracle_read
```

**Poznámka k formátu:** žádné `export`, žádné uvozovky kolem hodnot. Jen
`KEY=hodnota`. Shell si to doplní sám.

### Vytvoření .gitignore

**Co to je:** `.gitignore` říká gitu, které soubory nemá trackovat. Kritické
pro `.env` s tajnými klíči.

**Proč:** Abys omylem nepushl credentials na veřejný GitHub.

```bash
cat > .gitignore << 'EOF'
# Secrets
.env
.env.local

# Rust build artifacts
/target/
**/target/

# IDE
.vscode/
.idea/
*.swp

# OS
.DS_Store
Thumbs.db

# Logs
*.log
EOF
```

### Načtení .env do shellu

**Co to dělá:** Vezme všechny řádky z `.env` a udělá z nich shell proměnné,
dostupné v aktuálním terminálovém okně.

**Proč `set -a` / `set +a`:** `set -a` říká shellu "všechny nově načtené
proměnné automaticky exportuj". Po načtení to `set +a` vypne, aby to
neovlivňovalo další práci.

```bash
set -a && source .env && set +a
```

**Ověření že to funguje:**

```bash
echo $ZELA_KEY_ID
```

Mělo by vypsat Client ID. Pokud vypíše nic, `.env` se nenačetl správně.

**Důležité:** Tohle musíš udělat v každém novém terminálovém okně znovu.
Proměnné se dědí jen v rámci toho okna.

---

## Build WASM artefaktu

### Co to je WASM

**WebAssembly (WASM)** je binární formát, který umí běžet v sandboxu. Zela
spouští procedury jako WASM, protože:
- Je to bezpečné (sandbox, žádný access k filesystému)
- Je to rychlé (skoro nativní rychlost)
- Jde to kompilovat z Rustu

### Co znamená `wasm32-wasip2`

**Target triple** určuje, pro jakou platformu kompilovat.
- `wasm32` = 32-bitový WASM
- `wasip2` = WASI preview 2, což je standard API pro WASM mimo browser
  (file I/O, network atd.)

Zela explicitně vyžaduje tenhle target.

### Build

```bash
cd ~/code/zela_oracle_read_path_benchmark
cargo build --release --target wasm32-wasip2 -p oracle_read
```

**Argumenty:**
- `--release` = optimalizovaný build (menší, rychlejší, bez debug symbolů)
- `--target wasm32-wasip2` = kam kompilovat
- `-p oracle_read` = který package z workspace (máme jich zatím jen jeden,
  ale pro budoucnost je dobré být explicitní)

**Výstup:** `target/wasm32-wasip2/release/oracle_read.wasm` (~270 KB)

**Ověření:**

```bash
ls -la target/wasm32-wasip2/release/oracle_read.wasm
```

---

## Upload WASM na Zelu

Tohle má dvě části: získat JWT token, pak uploadnout soubor.

### Co je JWT token

**JWT (JSON Web Token)** je dočasný textový token, kterým se prokazujeme
při každém API volání na Zelu. Funguje to tak, že:

1. My dáme API key (client_id + secret) OAuth serveru
2. Server vrátí JWT, platný krátkou dobu (obvykle minuty)
3. JWT pak dáváme do hlavičky každého requestu

**Proč nedáváme API key přímo do requestu:** Bezpečnost. Kdyby někdo JWT
zachytil, vyprší za pár minut. API key je trvalý.

### Získání Builder JWT (pro upload)

**Scope `zela-builder:read zela-builder:write`** = "dovolují mi číst a zapisovat
do Builder modulu Zely". Builder je modul, který přijímá WASM uploady.

```bash
export ZELA_BUILDER_JWT=$(curl -sS --user "$ZELA_KEY_ID:$ZELA_KEY_SECRET" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=zela-builder:read zela-builder:write' \
  https://auth.zela.io/realms/zela/protocol/openid-connect/token \
  | jq -r .access_token)
```

**Rozklad příkazu:**
- `curl -sS` = silent (bez progress baru), ale ukaž errory
- `--user "ID:SECRET"` = Basic Auth (zakóduje base64 a pošle v hlavičce)
- `--data-urlencode` = form data, urlencoded (OAuth standard)
- `| jq -r .access_token` = z JSON odpovědi vytáhni jen pole `access_token`
- `export ZELA_BUILDER_JWT=...` = ulož výsledek do proměnné

**Ověření:**

```bash
echo $ZELA_BUILDER_JWT | head -c 50
```

Musí začínat `eyJ...` (standardní začátek JWT tokenu).

### Upload WASM

```bash
curl -f \
  -H "authorization: Bearer $ZELA_BUILDER_JWT" \
  -H 'content-type: application/wasm' \
  --data-binary '@target/wasm32-wasip2/release/oracle_read.wasm' \
  "https://core.zela.io/procedures/$ZELA_PROCEDURE/wasm?project=$ZELA_PROJECT&file_name=oracle_read.wasm"
```

**Rozklad:**
- `-f` = fail on HTTP error (jinak curl vrátí success i na 500)
- `-H "authorization: Bearer $JWT"` = hlavička s JWT
- `-H 'content-type: application/wasm'` = říkáme, že posíláme WASM soubor
- `--data-binary '@soubor.wasm'` = pošli obsah souboru raw (`@` = soubor, ne text)
- URL obsahuje `$ZELA_PROCEDURE` (jméno procedury) a `$ZELA_PROJECT` (UUID)

**Výsledek:** Zela přijme build, zkompiluje ho interně, a když je hotovo,
v dashboardu se objeví "Latest build status: Success" s revision hashem.

**V terminálu se nic nevypíše** (při `-f` a úspěchu nedělá curl žádný output).
Ověření se dělá v dashboardu.

---

## Volání procedury

### Najít Revision hash

Každý úspěšný build v Zele má **revision hash** — 40-znakový identifikátor,
který musíme použít při volání procedury. Bez hashe Zela neví, kterou verzi
spustit.

**Kde ho najdeš:** Zela dashboard → Procedures → oracle_read → Latest build detail.
Hash vypadá jako `683a03f46372c579f97952700ec2b89758999d86`.

### Získání Executor JWT (pro volání)

**Scope `zela-executor:call`** = "smím volat procedury přes Executor modul".
Executor je modul, který reálně spouští WASM proceduru.

**Proč jiný scope než u uploadu:** Principle of least privilege. Builder
scope neumí volat procedury, Executor scope neumí uploadovat. Oddělené
povinnosti.

```bash
export ZELA_EXECUTOR_JWT=$(curl -sS --user "$ZELA_KEY_ID:$ZELA_KEY_SECRET" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=zela-executor:call' \
  https://auth.zela.io/realms/zela/protocol/openid-connect/token \
  | jq -r .access_token)
```

### Zavolání procedury

```bash
curl -s --header "authorization: Bearer $ZELA_EXECUTOR_JWT" \
     --header 'Content-Type: application/json' \
     --data '{"jsonrpc":"2.0","id":1,"method":"zela.oracle_read#683a03f46372c579f97952700ec2b89758999d86","params":null}' \
     'https://executor.zela.io' | jq .
```

**Rozklad JSON-RPC bodyho:**
- `"jsonrpc": "2.0"` = protokol verze
- `"id": 1` = náš request ID (pro párování s odpovědí, libovolná hodnota)
- `"method": "zela.oracle_read#HASH"` = jméno procedury a revision
- `"params": null` = procedura M0 nebere žádné parametry

**Výsledek prvního volání:**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "genesis_hash": "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
    "account_pubkey": "H6ARHf6YXhGYeQfUzQNGk6rDNnLBQKrenN712K4AQJEG",
    "account_found": true,
    "account_data_len": 3312,
    "context_slot": 413891867,
    "wall_clock_start_ms": 1776457660525,
    "wall_clock_end_ms": 1776457660526,
    "wall_clock_elapsed_us": 690
  }
}
```

### Validace výsledku

| Pole | Význam | Check |
|---|---|---|
| `genesis_hash` | Solana network identifier | Rovná se mainnet genesis → potvrzeno že Zela routuje na mainnet |
| `account_pubkey` | Co jsme četli | Pyth SOL/USD account |
| `account_found` | Existuje ten account | true → account existuje na mainnetu |
| `account_data_len` | Kolik bajtů má data | 3312 = typická velikost Pyth legacy push oracle |
| `context_slot` | Slot, ve kterém RPC vrátilo data | 413891867 = plausibilní aktuální slot |
| `wall_clock_elapsed_us` | Kolik µs trvalo `getAccountInfo` | 690 µs = 0.69 ms, velmi rychle |

**M0 acceptance splněno.**

---

## Test variability (cold vs warm cache)

Pro ověření, že 690 µs není náhoda, pustili jsme 5 volání za sebou:

```bash
for i in 1 2 3 4 5; do
  curl -s --header "authorization: Bearer $ZELA_EXECUTOR_JWT" \
       --header 'Content-Type: application/json' \
       --data '{"jsonrpc":"2.0","id":1,"method":"zela.oracle_read#683a03f46372c579f97952700ec2b89758999d86","params":null}' \
       'https://executor.zela.io' | jq '.result.wall_clock_elapsed_us'
  sleep 1
done
```

**Výsledky:**

```
651
582
594
28549
524
```

**Interpretace:**
- 4 z 5 volání: **524–651 µs** (sub-millisecond, konzistentní)
- 1 outlier: **28549 µs** (28.5 ms) — pravděpodobně cold cache evict, network
  hiccup, nebo executor rescheduling

**Pro M3 benchmark:** Tohle je přesně důvod, proč budeme měřit **medián a p95**
místo průměru. Outliers jsou reálná součást tail latency a nesmí je průměr
překrýt.

**Pro M0:** Variabilita je v očekávaných mezích, pipeline funguje spolehlivě,
M0 hotovo.

---

## Kompletní sekvence příkazů (pro budoucí redeploy)

```bash
# 1. Načti .env
cd ~/code/zela_oracle_read_path_benchmark
set -a && source .env && set +a

# 2. Build
cargo build --release --target wasm32-wasip2 -p oracle_read

# 3. Získej Builder JWT
export ZELA_BUILDER_JWT=$(curl -sS --user "$ZELA_KEY_ID:$ZELA_KEY_SECRET" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=zela-builder:read zela-builder:write' \
  https://auth.zela.io/realms/zela/protocol/openid-connect/token \
  | jq -r .access_token)

# 4. Upload WASM
curl -f \
  -H "authorization: Bearer $ZELA_BUILDER_JWT" \
  -H 'content-type: application/wasm' \
  --data-binary '@target/wasm32-wasip2/release/oracle_read.wasm' \
  "https://core.zela.io/procedures/$ZELA_PROCEDURE/wasm?project=$ZELA_PROJECT&file_name=oracle_read.wasm"

# 5. Zjisti revision hash v dashboardu

# 6. Získej Executor JWT
export ZELA_EXECUTOR_JWT=$(curl -sS --user "$ZELA_KEY_ID:$ZELA_KEY_SECRET" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=zela-executor:call' \
  https://auth.zela.io/realms/zela/protocol/openid-connect/token \
  | jq -r .access_token)

# 7. Zavolej proceduru (nahraď REVISION_HASH)
curl -s --header "authorization: Bearer $ZELA_EXECUTOR_JWT" \
     --header 'Content-Type: application/json' \
     --data '{"jsonrpc":"2.0","id":1,"method":"zela.oracle_read#REVISION_HASH","params":null}' \
     'https://executor.zela.io' | jq .
```

---

## Klíčové learnings z M0

1. **JWTs jsou short-lived** — pokud mezi stepy uplyne víc než pár minut,
   musíš je regenerovat.
2. **Builder a Executor scopes jsou oddělené** — jedna volání, jiný scope.
3. **Revision hash je povinný v method name** — volání `zela.oracle_read`
   bez hashe vrátí "Method not found".
4. **Cold start je první volání po uploadu** — pomalejší, ignoruje se v benchmarku.
5. **Tail latency je reálná** — 1 z 5 volání bylo 50× pomalejší. Benchmark
   musí měřit p95, ne průměr.
6. **`context_slot` z raw RPC response** — high-level `RpcClient` ho
   neexponuje, proto používáme `call_rpc` a parsujeme response manuálně.
7. **`web-sys` pinning v Cargo.lock** — nutné kvůli kompatibilitě
   `wasm-component-ld` s `wasm-bindgen`. Bez toho build selže.

---

## Co dál

**M1:** Rozšíření na 10 sequential reads z různých Pyth feedů (SOL/USD,
ETH/USD, BTC/USD, USDC/USD, atd.) v jednom volání procedury.

**M2:** Externí baseline client (standardní Solana RPC z tvého počítače)
pro porovnání proti Zela path.

**M3:** Orchestrator + CSV, ~100 runů v různé denní doby.

**M4:** Analýza (medián, p95) + README s limity a výsledky.

**M5 (volitelně):** Publikace artefaktu, email Davidovi.
