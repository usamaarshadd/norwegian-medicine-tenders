from pathlib import Path
from urllib.request import Request, urlopen
import html
import re
import time
import unicodedata
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"
FIGURES = ROOT / "figures"
DMP_URL = "https://www.dmp.no/contentassets/fed1be54a81f4ec99a2329ca0fd0964c/legemiddelpriser-2026-08-03.xlsx"
PLAN_URL = "https://www.sykehusinnkjop.no/49574c/siteassets/dokumenter/legemidler/anskaffelsesplan/23-03-2026-anskaffelser-legemidler.xlsx"

MOLECULES = {
    "Axitinib": ({"L01EK01", "L01XE17"}, ("axitinib", "aksitinib"), "2607", "2607 Onkologi (patentert)"),
    "Everolimus": ({"L01EG02", "L04AH02"}, ("everolimus",), "2632a", "2632a Everolimus og Mykofenolsyre (enterotablett)"),
    "Lenalidomide": ({"L04AX04"}, ("lenalidomide", "lenalidomid"), "2707gj", "2707gj Onkologi ikke patentert"),
    "Anagrelide": ({"L01XX35"}, ("anagrelide", "anagrelid"), "2707gj", "2707gj Onkologi ikke patentert"),
    "Paliperidone": ({"N05AX13"}, ("paliperidone", "paliperidon"), "2601c", "2601c paliperidon"),
}

COLUMNS = [
    "noticeId", "tenderRef", "title", "country", "buyer", "productMolecule",
    "moleculeDetected", "moleculeVariant", "detectionMethod", "atcCode",
    "itemNumber", "productName", "strength", "packSize", "supplier", "maxPrice",
    "packsSoldLast12m", "estimatedValue", "awardedValue", "awardedSupplier",
    "currency", "noticeType", "status", "publicationDate", "contractStart",
    "procedureType", "sourceDocument", "sourceUrl"
]

def download(url, path):
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        return path
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "norway-pharma-pipeline/1.0"})
            with urlopen(request, timeout=60) as response:
                path.write_bytes(response.read())
            time.sleep(1)
            return path
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)

def clean(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()

def column(frame, name):
    name = clean(name)
    return next(value for value in frame.columns if clean(value).startswith(name))

def build(prices, plan):
    ingredient = column(prices, "Virkestoff")
    atc_column = column(prices, "ATC-kode")
    status_column = column(prices, "Markeds")
    item = column(prices, "Varenummer")
    product = column(prices, "Handelsnavn")
    supplier = column(prices, "Innehaver")
    strength = column(prices, "Styrke")
    pack_size = column(prices, "Mengde per beholder")
    price = column(prices, "Maks AIP")
    rows = []

    for molecule, (atc_codes, names, tender_ref, fallback_title) in MOLECULES.items():
        name_match = prices[ingredient].map(clean).apply(lambda value: any(clean(name) in value for name in names))
        atc_match = prices[atc_column].fillna("").isin(atc_codes)
        selected = prices[name_match | atc_match].copy()
        selected = selected[~selected[status_column].map(clean).str.contains("utg", na=False)]
        plan_rows = plan[plan.iloc[:, 0].astype(str).str.lower() == tender_ref.lower()]
        plan_row = plan_rows.iloc[0] if not plan_rows.empty else None
        title = str(plan_row.iloc[1]) if plan_row is not None else fallback_title
        published = plan_row.iloc[5] if plan_row is not None else pd.NaT
        contract_start = plan_row.iloc[7] if plan_row is not None else pd.NaT

        for _, row in selected.iterrows():
            found_by_name = any(clean(name) in clean(row[ingredient]) for name in names)
            rows.append({
                "noticeId": f"SIHF-{tender_ref}", "tenderRef": tender_ref, "title": title,
                "country": "NO", "buyer": "Sykehusinnkjøp HF", "productMolecule": molecule,
                "moleculeDetected": found_by_name, "moleculeVariant": row[ingredient],
                "detectionMethod": "name" if found_by_name else "ATC code",
                "atcCode": row[atc_column], "itemNumber": row[item], "productName": row[product],
                "strength": row[strength], "packSize": pd.to_numeric(row[pack_size], errors="coerce"),
                "supplier": row[supplier], "maxPrice": pd.to_numeric(row[price], errors="coerce"),
                "packsSoldLast12m": pd.NA, "estimatedValue": pd.NA, "awardedValue": pd.NA,
                "awardedSupplier": pd.NA, "currency": "NOK", "noticeType": "procurement plan",
                "status": row[status_column], "publicationDate": published,
                "contractStart": contract_start, "procedureType": pd.NA,
                "sourceDocument": "DMP price list and Sykehusinnkjøp procurement plan",
                "sourceUrl": f"{DMP_URL} | {PLAN_URL}"
            })

    result = pd.DataFrame(rows, columns=COLUMNS)
    for name in ("publicationDate", "contractStart"):
        result[name] = pd.to_datetime(result[name], errors="coerce").dt.strftime("%Y-%m-%d")
    result = result.drop_duplicates(["productMolecule", "itemNumber", "tenderRef"])
    return result.sort_values(["productMolecule", "itemNumber"])

def chart(values, title, label, path):
    values = values.sort_values()
    width, left, top, row_height = 900, 260, 75, 56
    height = top + row_height * len(values) + 55
    maximum = max(float(values.max()), 1)
    colors = ["#246b83", "#d47b29", "#3e8a45", "#7651a8", "#b3485b"]
    bars = []
    for index, (name, value) in enumerate(values.items()):
        y = top + index * row_height
        bar_width = 560 * float(value) / maximum
        bars.append(f'<text x="245" y="{y + 23}" text-anchor="end">{html.escape(str(name))}</text>')
        bars.append(f'<rect x="{left}" y="{y}" width="{bar_width:.1f}" height="32" rx="3" fill="{colors[index]}"/>')
        bars.append(f'<text x="{left + bar_width + 9:.1f}" y="{y + 23}">{float(value):,.1f}</text>')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">
<rect width="100%" height="100%" fill="white"/><style>text{{font:16px Arial;fill:#263238}}.title{{font:bold 25px Arial}}</style>
<text class="title" x="35" y="38">{html.escape(title)}</text>{''.join(bars)}
<text x="{left}" y="{height - 18}">{html.escape(label)}</text></svg>'''
    path.write_text(svg, encoding="utf-8")

def make_charts(data):
    FIGURES.mkdir(exist_ok=True)
    chart(data.groupby("productMolecule").size(), "Pack count", "packs", FIGURES / "01_pack_count.svg")
    chart(data.groupby("productMolecule")["maxPrice"].median(), "Median maximum price", "NOK per pack", FIGURES / "02_median_price.svg")
    chart(data.groupby("productMolecule")["supplier"].nunique(), "Supplier count", "suppliers", FIGURES / "03_supplier_count.svg")
    summary = pd.DataFrame({
        "packs": data.groupby("productMolecule").size(),
        "suppliers": data.groupby("productMolecule")["supplier"].nunique(),
        "price": data.groupby("productMolecule")["maxPrice"].median()
    })
    score = summary["packs"].rank(pct=True) + summary["price"].rank(pct=True) - summary["suppliers"].rank(pct=True)
    chart(score, "Opportunity score", "relative score", FIGURES / "04_opportunity_score.svg")

def main():
    prices_file = download(DMP_URL, DATA / "dmp_prices.xlsx")
    plan_file = download(PLAN_URL, DATA / "procurement_plan.xlsx")
    prices = pd.read_excel(prices_file, header=2, dtype={0: str})
    plan = pd.read_excel(plan_file, sheet_name="Anskaffelser Legemidler", header=3)
    result = build(prices, plan)
    result.to_csv(ROOT / "output.csv", index=False, encoding="utf-8", na_rep="")
    make_charts(result)
    print(f"Created output.csv with {len(result)} rows")

if __name__ == "__main__":
    main()
