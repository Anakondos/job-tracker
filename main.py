from datetime import datetime

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from parsers.greenhouse import fetch_greenhouse
from parsers.lever import fetch_lever
from parsers.smartrecruiters import fetch_smartrecruiters  # если файла нет — закомментируй
from storage import load_profile

# Кэш статуса по компаниям: "profile:company_id" -> {ok, error, checked_at, ats, url}
company_fetch_status: dict[str, dict] = {}

app = FastAPI(
    title="Job Tracker",
    description="Job aggregator for Product / Program / Project roles from ATS boards",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


def _cfg_company_id(cfg: dict) -> str:
    return str(cfg.get("id") or cfg.get("company_id") or cfg.get("name") or cfg.get("company") or "").strip()


def _cfg_company_name(cfg: dict) -> str:
    return str(cfg.get("name") or cfg.get("company") or "").strip()


def _cfg_company_ats(cfg: dict) -> str:
    return str(cfg.get("ats") or "").strip().lower()


def _cfg_company_url(cfg: dict) -> str:
    # новый формат: board_url, старый: url
    return str(cfg.get("board_url") or cfg.get("url") or "").strip()


def _is_us_location(location: str | None) -> bool:
    if not location:
        return False
    loc = location.lower()

    # явные маркеры
    if "united states" in loc or "usa" in loc:
        return True
    # remote usa
    if "remote" in loc and ("us" in loc or "u.s" in loc or "united states" in loc):
        return True

    # грубая эвристика по штатам (двухбуквенные коды обычно после запятой)
    us_markers = [
        ", al", ", ak", ", az", ", ar", ", ca", ", co", ", ct", ", de", ", fl", ", ga",
        ", hi", ", ia", ", id", ", il", ", in", ", ks", ", ky", ", la", ", ma", ", md",
        ", me", ", mi", ", mn", ", mo", ", ms", ", mt", ", nc", ", nd", ", ne", ", nh",
        ", nj", ", nm", ", nv", ", ny", ", oh", ", ok", ", or", ", pa", ", ri", ", sc",
        ", sd", ", tn", ", tx", ", ut", ", va", ", vt", ", wa", ", wi", ", wv", ", wy",
        "washington, dc",
    ]
    return any(m in loc for m in us_markers)


def _mark_company_status(profile: str, cfg: dict, ok: bool, error: str | None = None):
    company_id = _cfg_company_id(cfg)
    key = f"{profile}:{company_id}"
    company_fetch_status[key] = {
        "ok": ok,
        "error": error or "",
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "ats": _cfg_company_ats(cfg),
        "url": _cfg_company_url(cfg),
        "company": _cfg_company_name(cfg),
        "id": company_id,
    }


def _fetch_for_company(profile: str, cfg: dict) -> list[dict]:
    """
    Унифицированный вызов парсеров + запись статуса компании.
    """
    company = _cfg_company_name(cfg)
    ats = _cfg_company_ats(cfg)
    url = _cfg_company_url(cfg)

    # если не хватает конфигурации — сразу фиксируем ошибку (чтобы видно было в /companies)
    if not company or not ats or not url:
        _mark_company_status(
            profile,
            cfg,
            ok=False,
            error=f"Missing config fields (company='{company}', ats='{ats}', url='{url}')",
        )
        return []

    try:
        if ats == "greenhouse":
            jobs = fetch_greenhouse(company, url)
        elif ats == "lever":
            jobs = fetch_lever(company, url)
        elif ats == "smartrecruiters":
            jobs = fetch_smartrecruiters(company, url)
        else:
            jobs = []

        _mark_company_status(profile, cfg, ok=True)

        # мета-инфо к каждой вакансии
        for j in jobs:
            j["company"] = company
            j["company_id"] = _cfg_company_id(cfg)
            j["ats"] = ats
            j["industry"] = cfg.get("industry", "")
            j["tags"] = cfg.get("tags", [])

        return jobs

    except Exception as e:  # noqa: BLE001
        _mark_company_status(profile, cfg, ok=False, error=str(e))
        print(f"Error for {company}: {e}")
        return []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/jobs")
async def get_jobs(
    profile: str = Query("all", description="Имя профиля из папки profiles/*.json"),
    ats_filter: str = Query("all", description="all / greenhouse / lever / smartrecruiters"),
    role_filter: str = Query("all", description="all / product / program / project / other"),
    location_filter: str = Query("all", description="all / us / nonus"),
    company_filter: str = Query("", description="подстрока в названии компании"),
    search: str = Query("", description="поиск по title+location+company"),
    state: str = Query("", description="Filter by US state, e.g. NC (substring match)"),
    city: str = Query("", description="Filter by city, e.g. Raleigh (substring match)"),
):
    companies_cfg = load_profile(profile)
    all_jobs: list[dict] = []

    for cfg in companies_cfg:
        ats = _cfg_company_ats(cfg)
        if ats_filter != "all" and ats_filter != ats:
            continue
        all_jobs.extend(_fetch_for_company(profile, cfg))

    def match_role(title: str | None) -> bool:
        if role_filter == "all":
            return True
        if not title:
            return False
        t = title.lower()

        # базовые семейства ролей
        product_keys = ["product manager", "product owner", "product", "pm "]
        program_keys = ["technical program", "program manager", "tpm", "program"]
        project_keys = ["project manager", "project", "delivery", "scrum master"]
        # “other” = всё, что НЕ попало в три семейства
        is_product = any(k in t for k in product_keys)
        is_program = any(k in t for k in program_keys)
        is_project = any(k in t for k in project_keys)

        if role_filter == "product":
            return is_product
        if role_filter == "program":
            return is_program
        if role_filter == "project":
            return is_project
        if role_filter == "other":
            return not (is_product or is_program or is_project)

        return True

    def match_location(loc: str | None) -> bool:
        if location_filter == "all":
            return True
        is_us = _is_us_location(loc)
        if location_filter == "us":
            return is_us
        if location_filter == "nonus":
            return not is_us
        return True

    def match_company(name: str | None) -> bool:
        if not company_filter:
            return True
        if not name:
            return False
        return company_filter.lower() in name.lower()

    def match_search(job: dict) -> bool:
        if not search:
            return True
        s = search.lower()
        haystack = f"{job.get('title','')} {job.get('location','')} {job.get('company','')}".lower()
        return s in haystack

    def match_state(location: str | None) -> bool:
        if not state:
            return True
        if not location:
            return False
        return state.lower() in location.lower()

    def match_city(location: str | None) -> bool:
        if not city:
            return True
        if not location:
            return False
        return city.lower() in location.lower()

    filtered: list[dict] = []
    for j in all_jobs:
        if not match_role(j.get("title")):
            continue
        if not match_location(j.get("location")):
            continue
        if not match_company(j.get("company")):
            continue
        if not match_search(j):
            continue
        if not match_state(j.get("location")):
            continue
        if not match_city(j.get("location")):
            continue
        filtered.append(j)

    filtered.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    return {"count": len(filtered), "jobs": filtered}


@app.get("/companies")
def get_companies(profile: str = Query("all", description="Имя профиля из profiles/*.json")):
    companies_cfg = load_profile(profile)
    items: list[dict] = []

    for cfg in companies_cfg:
        company_id = _cfg_company_id(cfg)
        key = f"{profile}:{company_id}"
        st = company_fetch_status.get(key, {})

        items.append(
            {
                "id": company_id,
                "company": _cfg_company_name(cfg),
                "ats": _cfg_company_ats(cfg),
                "url": _cfg_company_url(cfg),
                "tags": cfg.get("tags", []),
                "priority": cfg.get("priority", 0),
                "hq_state": cfg.get("hq_state"),
                "last_ok": st.get("ok", None),
                "last_error": st.get("error", ""),
                "last_checked": st.get("checked_at", ""),
            }
        )

    return {"count": len(items), "companies": items}


@app.get("/profiles/{name}")
async def get_profile_companies(name: str):
    companies = load_profile(name)
    result_companies = []
    for c in companies:
        result_companies.append(
            {
                "id": c.get("id") or c.get("company_id"),
                "name": c.get("name") or c.get("company"),
                "tags": c.get("tags", []),
                "priority": c.get("priority", 0),
                "ats": c.get("ats"),
                "board_url": c.get("board_url") or c.get("url"),
            }
        )
    return JSONResponse({"count": len(result_companies), "companies": result_companies})
