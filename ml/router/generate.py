"""Generate labeled first messages for the router: Spanish (MX, CO, AR) and Brazilian Portuguese.

Why generated: the dataset has no dispute conversations (call transcripts are two balance-inquiry templates and
`main_topics` leaks the label; docs/data_findings.md H24-H25). The label of every message is the intent of the
template it was rendered from, never a model's judgment.

Two disjoint template families per label and language, each with its own merchants and product nouns:
family A is split by template into train and val; family B is used only for `test_unseen`, so that split
measures generalization to phrasings and merchants the model never saw, not template memorization.
Deterministic: the same seed gives byte-identical files.
"""

from __future__ import annotations

import argparse
import json
import random
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

GENERATOR_VERSION = "router-gen-1"
SEED = 20261003
LABELS = ("not_me", "wrong_amount", "duplicate", "not_received", "out_of_scope")
LABEL_WEIGHTS = (0.19, 0.19, 0.19, 0.19, 0.24)
REGIONS = {"es": ("MX", "CO", "AR"), "pt": ("BR",)}
LANG_WEIGHTS = {"es": 0.6, "pt": 0.4}
DATA_DIR = Path(__file__).with_name("data")
SPLIT_SIZES = {"train": 3000, "val": 600, "test_unseen": 600}
VAL_TEMPLATES_PER_CELL = 2  # family A templates held out (per label and language) for val

# Merchant names as they appear in silver `transactions.merchant_name`, split so that no merchant is in both families.
MERCHANTS = {
    "A": (
        "Super Ahorro",
        "Tienda Don José",
        "Restaurante El Buen Sabor",
        "Empresa Telefónica",
        "Servicios Públicos",
        "Uber",
        "Estación de Servicio",
        "Cine Premium",
        "Streaming Music",
        "Tienda General",
        "Farmacia Salud",
        "Clínica Médica",
    ),
    "B": (
        "Mercado Central",
        "Cable TV",
        "Internet Plus",
        "Taxi Seguro",
        "Gasolinera Express",
        "Ferretería",
        "Boutique Moda",
        "Centro Comercial",
        "Conciertos Live",
        "Teatro Nacional",
        "Laboratorio Central",
        "Óptica Visión",
    ),
}

ITEMS = {
    ("A", "es"): ("el pedido", "el producto", "la mercancía", "mi compra"),
    ("B", "es"): ("el paquete", "la orden", "el artículo", "lo que compré"),
    ("A", "pt"): ("o pedido", "o produto", "a mercadoria", "minha compra"),
    ("B", "pt"): ("o pacote", "a encomenda", "o item", "o que comprei"),
}

DATES = {
    "es": (
        "ayer",
        "anteayer",
        "el viernes",
        "el lunes pasado",
        "el 12 de junio",
        "el 3 de mayo",
        "la semana pasada",
        "hoy en la mañana",
        "hace tres días",
        "el fin de semana",
    ),
    "pt": (
        "ontem",
        "anteontem",
        "na sexta",
        "segunda passada",
        "dia 12 de junho",
        "dia 3 de maio",
        "semana passada",
        "hoje de manhã",
        "há três dias",
        "no fim de semana",
    ),
}

OPENERS = {
    "MX": ("", "", "Hola, ", "Buenas tardes, ", "Oiga, ", "Qué tal, "),
    "CO": ("", "", "Buenos días, ", "Hola, qué pena, ", "Buenas, ", "Señores, "),
    "AR": ("", "", "Hola che, ", "Buenas, ", "Hola, mirá, ", "Buen día, "),
    "BR": ("", "", "Oi, ", "Olá, ", "Bom dia, ", "Boa tarde, "),
}
CLOSERS = {
    "MX": ("", "", "", " por favor", ". ¿Me ayudan?", ", gracias"),
    "CO": ("", "", "", ", me colabora por favor", ", muchas gracias", ". ¿Qué hago?"),
    "AR": ("", "", "", ". ¿Me das una mano?", ", gracias", ". ¿Qué hago?"),
    "BR": ("", "", "", " por favor", ". Podem me ajudar?", ", obrigado", ", obrigada"),
}
# Regional lexical swaps applied with probability 0.5 (voseo in Argentina, "checar" in Mexico, ...).
REGIONAL_SWAPS = {
    "MX": (("revisen", "chequen"), ("revisar", "checar"), ("dinero", "lana")),
    "CO": (("dinero", "plata"), ("revisen", "verifiquen")),
    "AR": (("puedes", "podés"), ("tienes", "tenés"), ("dinero", "plata"), ("revisen", "fíjense")),
    "BR": (("cartão", "cartão de crédito"), ("dinheiro", "grana")),
}
CURRENCY = {"MX": "MXN", "CO": "COP", "AR": "ARS"}

# Slots: {m} merchant, {a} amount (the agreed/legit one), {a2} a different amount, {d} date, {item} product noun.
TEMPLATES: dict[str, dict[str, dict[str, tuple[str, ...]]]] = {
    "A": {
        "es": {
            "not_me": (
                "yo no hice esa compra en {m} de {a}",
                "me aparece un cargo de {a} en {m} que no reconozco",
                "no reconozco un cobro de {m} del {d}",
                "hay un cargo en mi tarjeta de {m} por {a} y yo no fui",
                "alguien usó mi tarjeta en {m} {d}, yo no autoricé ese pago",
                "creo que es fraude, tengo una compra de {a} en {m} que nunca hice",
                "ese movimiento de {m} no es mío",
                "{d} me llegó una notificación de compra en {m} por {a} pero yo no compré nada",
                "no realicé ningún pago en {m}, quiero desconocer ese cargo",
                "me están cobrando {a} de {m} y nunca he comprado ahí",
            ),
            "wrong_amount": (
                "en {m} me cobraron {a2} pero la cuenta era de {a}",
                "el cargo de {m} está mal, pagué {a} y me aparece {a2}",
                "me cobraron de más en {m} {d}, el monto correcto era {a}",
                "la compra en {m} era por {a} y me descontaron {a2}",
                "el monto que me cobró {m} no coincide con lo que acordamos",
                "{d} pagué en {m} y me cargaron un importe diferente: {a2} en vez de {a}",
                "me cobraron una cantidad mayor a la del ticket en {m}",
                "el precio era {a} pero en mi estado de cuenta sale {a2} de {m}",
                "{m} me hizo un cobro por un monto incorrecto",
                "quiero reclamar la diferencia, {m} me cobró {a2} y eran {a}",
            ),
            "duplicate": (
                "me cobraron dos veces la misma compra en {m}",
                "el cargo de {a} en {m} aparece duplicado",
                "{d} pagué en {m} una sola vez y me salen dos cargos de {a}",
                "tengo un cobro repetido de {m}",
                "me descontaron doble el pago de {m}",
                "aparece dos veces el mismo movimiento de {a} de {m}",
                "en {m} me pasaron la tarjeta y se cobró por duplicado",
                "hice una compra en {m} y se registró dos veces",
                "me cobraron {a} dos veces en {m} {d}",
                "hay un doble cargo de {m} en mi cuenta",
            ),
            "not_received": (
                "pagué {a} en {m} y nunca me llegó {item}",
                "compré en {m} {d} y todavía no recibo {item}",
                "me cobraron {item} de {m} pero nunca lo recibí",
                "{m} me prometió un reembolso de {a} y nunca llegó",
                "pagué el servicio de {m} y no me lo prestaron",
                "ya pasaron semanas y {m} no me entregó lo que pagué",
                "hice el pago de {a} a {m} y no recibí nada",
                "cancelé en {m} y el reembolso nunca apareció en mi cuenta",
                "me debitaron {a} de {m} pero {item} nunca llegó",
                "pagué {d} en {m} y no me dieron el producto",
            ),
            "out_of_scope": (
                "¿cuál es el saldo de mi cuenta?",
                "quiero saber cuánto tengo disponible en mi tarjeta",
                "¿cómo solicito un préstamo personal?",
                "necesito una tarjeta nueva",
                "quiero cambiar mi dirección",
                "¿a qué hora abren las sucursales?",
                "quiero hablar con un asesor",
                "¿cuánto gasté en {m} este mes?",
                "¿me pueden aumentar el límite de crédito?",
                "¿cómo activo la banca en línea?",
                "quiero pagar mi tarjeta de crédito, ¿cuánto debo?",
                "olvidé mi clave del cajero",
            ),
        },
        "pt": {
            "not_me": (
                "não reconheço essa compra de {a} na {m}",
                "eu não fiz essa compra na {m}",
                "apareceu uma cobrança da {m} que eu não fiz",
                "tem um débito de {a} da {m} no meu cartão e não fui eu",
                "alguém usou meu cartão na {m} {d}",
                "acho que é fraude, tem uma compra na {m} que eu nunca fiz",
                "essa transação da {m} não é minha",
                "recebi notificação de compra na {m} de {a} mas eu não comprei nada",
                "não autorizei esse pagamento para {m}",
                "estão me cobrando {a} da {m} e eu nunca comprei lá",
            ),
            "wrong_amount": (
                "na {m} me cobraram {a2} mas a conta era {a}",
                "a cobrança da {m} está errada, paguei {a} e aparece {a2}",
                "fui cobrado a mais na {m} {d}, o valor certo era {a}",
                "a compra na {m} era de {a} e descontaram {a2}",
                "o valor que a {m} cobrou não é o combinado",
                "{d} paguei na {m} e cobraram um valor diferente: {a2} em vez de {a}",
                "me cobraram mais do que estava no recibo da {m}",
                "o preço era {a} mas na fatura aparece {a2} da {m}",
                "a {m} fez uma cobrança com valor incorreto",
                "quero contestar a diferença, a {m} cobrou {a2} e era {a}",
            ),
            "duplicate": (
                "fui cobrado duas vezes pela mesma compra na {m}",
                "a cobrança de {a} da {m} está duplicada",
                "{d} paguei uma vez só na {m} e aparecem duas cobranças de {a}",
                "tenho uma cobrança repetida da {m}",
                "descontaram em dobro o pagamento da {m}",
                "aparece duas vezes a mesma transação de {a} da {m}",
                "passaram o cartão na {m} e cobrou em duplicidade",
                "fiz uma compra na {m} e foi registrada duas vezes",
                "me cobraram {a} duas vezes na {m} {d}",
                "tem cobrança dupla da {m} na minha conta",
            ),
            "not_received": (
                "paguei {a} na {m} e nunca chegou {item}",
                "comprei na {m} {d} e ainda não recebi {item}",
                "fui cobrado por {item} da {m} mas nunca recebi",
                "a {m} prometeu um reembolso de {a} e nunca caiu",
                "paguei o serviço da {m} e não foi prestado",
                "já faz semanas e a {m} não entregou o que eu paguei",
                "fiz o pagamento de {a} para a {m} e não recebi nada",
                "cancelei na {m} e o estorno nunca apareceu",
                "debitaram {a} da {m} mas {item} nunca chegou",
                "paguei {d} na {m} e não me deram o produto",
            ),
            "out_of_scope": (
                "qual é o saldo da minha conta?",
                "quero saber quanto tenho de limite disponível no cartão",
                "como faço para pedir um empréstimo?",
                "preciso de um cartão novo",
                "quero mudar meu endereço",
                "que horas abre a agência?",
                "quero falar com um atendente",
                "quanto gastei na {m} este mês?",
                "vocês podem aumentar meu limite?",
                "como ativo o internet banking?",
                "quero pagar a fatura do cartão, quanto devo?",
                "esqueci a senha do caixa eletrônico",
            ),
        },
    },
    "B": {
        "es": {
            "not_me": (
                "desconozco un consumo de {a} hecho en {m}",
                "no fui yo quien compró en {m}, revisen por favor",
                "tengo un gasto raro en {m} {d}, no lo hice yo",
                "¿me hackearon la tarjeta? sale un pago a {m} que no hice",
                "un tercero pagó con mi tarjeta en {m}",
                "nunca estuve en {m} y me sale una compra de {a}",
                "ese consumo en {m} no lo reconozco para nada",
                "sospecho que clonaron mi tarjeta, hay una transacción de {m} que no es mía",
                "jamás autoricé un pago de {a} a {m}",
                "me llegó un aviso de cargo de {m} y tengo la tarjeta conmigo, no fui yo",
            ),
            "wrong_amount": (
                "{m} me facturó {a2} cuando el precio acordado era {a}",
                "el importe en {m} está equivocado",
                "me pasaron un monto distinto en {m}: debía ser {a}",
                "pagué con tarjeta en {m} y el valor cobrado es más alto del que firmé",
                "en el voucher de {m} dice {a} pero el banco me cobró {a2}",
                "{m} me cargó {a2}, es mucho más de lo que costaba",
                "hay un error en el monto del cargo de {m} del {d}",
                "no me cuadra el valor que cobró {m}, era {a}",
                "me sobrecobraron en {m}",
                "la cifra que aparece de {m} no es la que pagué, es mayor",
            ),
            "duplicate": (
                "{m} me cargó el mismo consumo dos veces",
                "veo dos transacciones iguales de {a} en {m}",
                "se duplicó mi pago en {m} {d}",
                "el cobro de {m} salió doble",
                "me facturaron por partida doble en {m}",
                "tengo el mismo cargo de {m} repetido, solo compré una vez",
                "en mi resumen figura dos veces {m} por {a}",
                "{m} cobró una vez de más el mismo importe",
                "me debitaron {a} y otra vez {a} en {m} por la misma compra",
                "pasaron la tarjeta dos veces en {m}",
            ),
            "not_received": (
                "{m} nunca me mandó {item} que pagué {d}",
                "sigo esperando {item} de {m} y ya me cobraron",
                "el reembolso de {m} no me ha caído",
                "pagué por adelantado en {m} y no me cumplieron",
                "me cobraron {a} en {m} por un servicio que nunca se prestó",
                "{m} me dijo que me devolvían el dinero y no ha pasado nada",
                "no me entregaron {item}, pero el cargo de {m} sí salió",
                "aboné {a} a {m} y no recibí ni el producto ni la devolución",
                "la devolución de {m} por {a} no aparece",
                "{m} canceló mi pedido pero no me regresaron el dinero",
            ),
            "out_of_scope": (
                "¿me dicen mi saldo por favor?",
                "quiero consultar los movimientos de mi cuenta de ahorros",
                "¿qué requisitos piden para un crédito hipotecario?",
                "se me venció la tarjeta, ¿cómo pido la reposición?",
                "necesito actualizar mi número de teléfono",
                "¿dónde queda el cajero más cercano?",
                "pásame con una persona por favor",
                "¿la compra en {m} ya se reflejó?",
                "quiero abrir una cuenta para mi hijo",
                "¿qué tasa de interés tiene la tarjeta de crédito?",
                "¿puedo hacer una transferencia internacional desde la app?",
                "quiero bloquear mi tarjeta porque la perdí",
            ),
        },
        "pt": {
            "not_me": (
                "desconheço um gasto de {a} feito na {m}",
                "não fui eu que comprei na {m}, verifiquem por favor",
                "tem um gasto estranho na {m} {d}, não fui eu",
                "clonaram meu cartão? aparece um pagamento para {m} que eu não fiz",
                "um terceiro pagou com meu cartão na {m}",
                "nunca estive na {m} e aparece uma compra de {a}",
                "esse gasto na {m} eu não reconheço de jeito nenhum",
                "suspeito de clonagem, tem uma transação da {m} que não é minha",
                "jamais autorizei um pagamento de {a} para {m}",
                "chegou um aviso de compra da {m} e o cartão está comigo, não fui eu",
            ),
            "wrong_amount": (
                "a {m} me cobrou {a2} quando o preço combinado era {a}",
                "o valor da {m} está errado",
                "passaram um valor diferente na {m}: devia ser {a}",
                "paguei no cartão na {m} e o valor cobrado é maior do que eu assinei",
                "no comprovante da {m} diz {a} mas o banco cobrou {a2}",
                "a {m} lançou {a2}, é bem mais do que custava",
                "tem um erro no valor da cobrança da {m} de {d}",
                "o valor que a {m} cobrou não bate, era {a}",
                "fui sobretaxado na {m}",
                "o número que aparece da {m} não é o que eu paguei, é maior",
            ),
            "duplicate": (
                "a {m} lançou o mesmo gasto duas vezes",
                "vejo duas transações iguais de {a} na {m}",
                "meu pagamento na {m} duplicou {d}",
                "a cobrança da {m} veio dobrada",
                "me faturaram em dobro na {m}",
                "tenho a mesma cobrança da {m} repetida, só comprei uma vez",
                "no meu extrato aparece duas vezes {m} de {a}",
                "a {m} cobrou uma vez a mais o mesmo valor",
                "debitaram {a} e de novo {a} na {m} pela mesma compra",
                "passaram meu cartão duas vezes na {m}",
            ),
            "not_received": (
                "a {m} nunca mandou {item} que paguei {d}",
                "continuo esperando {item} da {m} e já fui cobrado",
                "o reembolso da {m} não caiu",
                "paguei adiantado na {m} e não cumpriram",
                "me cobraram {a} na {m} por um serviço que nunca foi feito",
                "a {m} disse que ia devolver o dinheiro e nada",
                "não me entregaram {item}, mas a cobrança da {m} veio",
                "paguei {a} para a {m} e não recebi nem o produto nem a devolução",
                "a devolução da {m} de {a} não aparece",
                "a {m} cancelou meu pedido mas não devolveram o dinheiro",
            ),
            "out_of_scope": (
                "me informa meu saldo por favor?",
                "quero consultar o extrato da poupança",
                "quais documentos precisa para um financiamento imobiliário?",
                "meu cartão venceu, como peço outro?",
                "preciso atualizar meu telefone",
                "onde fica o caixa eletrônico mais próximo?",
                "me passa para uma pessoa por favor",
                "a compra na {m} já foi processada?",
                "quero abrir uma conta para meu filho",
                "qual é a taxa de juros do cartão de crédito?",
                "posso fazer transferência internacional pelo app?",
                "quero bloquear meu cartão porque perdi",
            ),
        },
    },
}


@dataclass(frozen=True)
class Example:
    text: str
    label: str
    lang: str
    region: str
    template_id: str
    provenance: str
    generator_version: str


def _thousands(value: int, sep: str) -> str:
    return f"{value:,}".replace(",", sep)


def format_amount(rng: random.Random, value: float, region: str) -> str:
    """One amount in a format customers actually type; the formats differ by language and country."""
    whole = int(value)
    cents = f"{round((value - whole) * 100):02d}"
    if region == "BR":
        options = [
            f"R$ {_thousands(whole, '.')},{cents}",
            f"R${whole}",
            f"R$ {whole}",
            f"{whole} reais",
            f"{_thousands(whole, '.')},{cents}",
        ]
        if whole % 100 == 0:
            options += ["cem reais" if whole == 100 else "mil reais" if whole == 1000 else f"{whole} reais"]
        return rng.choice(options)
    options = [
        f"{whole}.{cents}",
        f"${_thousands(whole, ',')}",
        f"${_thousands(whole, ',')}.{cents}",
        f"{_thousands(whole, '.')},{cents}",
        f"$ {_thousands(whole, '.')}",
        f"{whole} pesos",
        f"{_thousands(whole, ',')} {CURRENCY[region]}",
    ]
    if whole % 500 == 0:
        words = {500: "quinientos pesos", 1000: "mil pesos", 2000: "dos mil pesos", 1500: "mil quinientos pesos"}
        options += [words.get(whole, f"{whole} pesos")]
    return rng.choice(options)


def _random_value(rng: random.Random) -> float:
    if rng.random() < 0.2:
        return float(rng.choice((100, 500, 1000, 1500, 2000)))
    return round(rng.uniform(5, 5000), 2)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _typo(rng: random.Random, word: str) -> str:
    if len(word) < 4:
        return word
    i = rng.randrange(1, len(word) - 1)
    kind = rng.choice(("swap", "drop", "double"))
    if kind == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2 :]
    if kind == "drop":
        return word[:i] + word[i + 1 :]
    return word[:i] + word[i] + word[i:]


def add_noise(rng: random.Random, text: str) -> str:
    """Typing noise seen in chat: lowercase, missing accents and punctuation, one or two typos."""
    if rng.random() < 0.3:
        text = text.lower()
    if rng.random() < 0.25:
        text = _strip_accents(text)
    if rng.random() < 0.2:
        text = "".join(c for c in text if c not in "¿?¡!.,:")
    if rng.random() < 0.15:
        words = text.split(" ")
        for _ in range(rng.choice((1, 2))):
            j = rng.randrange(len(words))
            words[j] = _typo(rng, words[j])
        text = " ".join(words)
    return text


def render(rng: random.Random, template: str, family: str, lang: str, region: str) -> str:
    value = _random_value(rng)
    other = round(value * rng.uniform(1.1, 3.0), 2)
    merchant = rng.choice(MERCHANTS[family])
    if rng.random() < 0.15:
        merchant = merchant.lower()
    body = template.format(
        m=merchant,
        a=format_amount(rng, value, region),
        a2=format_amount(rng, other, region),
        d=rng.choice(DATES[lang]),
        item=rng.choice(ITEMS[(family, lang)]),
    )
    for old, new in REGIONAL_SWAPS[region]:
        if old in body and rng.random() < 0.5:
            body = body.replace(old, new)
    opener = rng.choice(OPENERS[region])
    if not opener and rng.random() < 0.6:
        body = body[0].upper() + body[1:]
    text = f"{opener}{body}{rng.choice(CLOSERS[region])}"
    return add_noise(rng, text)


def template_ids(family: str) -> list[tuple[str, str, str, str]]:
    """Every (template_id, label, lang, template) of a family, in a fixed order."""
    out = []
    for lang, by_label in TEMPLATES[family].items():
        for label in LABELS:
            for i, template in enumerate(by_label[label]):
                out.append((f"{family}-{lang}-{label}-{i:02d}", label, lang, template))
    return out


def split_family_a(seed: int = SEED) -> tuple[set[str], set[str]]:
    """Template ids of family A for train and for val: whole templates are held out, never single rows."""
    rng = random.Random(seed)
    val: set[str] = set()
    for lang in TEMPLATES["A"]:
        for label in LABELS:
            ids = [f"A-{lang}-{label}-{i:02d}" for i in range(len(TEMPLATES["A"][lang][label]))]
            val.update(rng.sample(ids, VAL_TEMPLATES_PER_CELL))
    train = {tid for tid, *_ in template_ids("A")} - val
    return train, val


def generate_split(allowed: set[str], family: str, n: int, seed: int) -> list[Example]:
    rng = random.Random(seed)
    pool: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for tid, label, lang, template in template_ids(family):
        if tid in allowed:
            pool.setdefault((label, lang), []).append((tid, template))
    seen: set[str] = set()
    out: list[Example] = []
    attempts = 0
    while len(out) < n:
        attempts += 1
        if attempts > n * 50:
            raise RuntimeError(f"could not generate {n} distinct messages for family {family}")
        label = rng.choices(LABELS, weights=LABEL_WEIGHTS)[0]
        lang = rng.choices(list(LANG_WEIGHTS), weights=list(LANG_WEIGHTS.values()))[0]
        region = rng.choice(REGIONS[lang])
        tid, template = rng.choice(pool[(label, lang)])
        text = render(rng, template, family, lang, region)
        if text in seen:
            continue
        seen.add(text)
        out.append(Example(text, label, lang, region, tid, "generated", GENERATOR_VERSION))
    return out


def generate_all(seed: int = SEED) -> dict[str, list[Example]]:
    train_ids, val_ids = split_family_a(seed)
    all_b = {tid for tid, *_ in template_ids("B")}
    return {
        "train": generate_split(train_ids, "A", SPLIT_SIZES["train"], seed + 1),
        "val": generate_split(val_ids, "A", SPLIT_SIZES["val"], seed + 2),
        "test_unseen": generate_split(all_b, "B", SPLIT_SIZES["test_unseen"], seed + 3),
    }


def write_jsonl(path: Path, rows: list[Example]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    for split, rows in generate_all(args.seed).items():
        write_jsonl(args.out / f"{split}.jsonl", rows)
        print(f"{split}: {len(rows)} rows -> {args.out / f'{split}.jsonl'}")


if __name__ == "__main__":
    main()
