"""Optional read-only sources. Secrets never enter raw URLs, snapshots, or logs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .providers import number, trace
from .store import now

class DartDailyBudgetExceeded(RuntimeError):
    """Raised before an OpenDART request exceeds the local daily safety budget."""


class DartProviderUnavailable(RuntimeError):
    """Provider maintenance; a shared retry checkpoint limits repeated requests."""


DART_REPORT_SECTIONS = (
    "shareholders",
    "dividends",
    "shares",
    "employees",
    "executives",
    "minority_shareholders",
    "holder_changes",
    "auditor",
    "treasury",
    "investments",
)

DART_FINANCIAL_REPORT_CODES = {
    "11013": (5, 15),  # first quarter
    "11012": (8, 15),  # half year
    "11014": (11, 15),  # third quarter
    "11011": (4, 15),  # annual report, in the following year
}
DART_FINANCIAL_INDICATOR_CLASSES = (
    "M210000",  # profitability
    "M220000",  # stability
    "M230000",  # growth
    "M240000",  # activity
)


def order_financial_tasks(tasks, progress, requested=None):
    """Advance unseen periods while reserving every fifth visit for rechecks."""
    visits = sum(int(state.get('attempts', 1)) for state in progress.values())
    recheck = visits % 5 == 4
    def priority(task):
        key = task['query_key']
        seen = bool(progress.get(key, {}).get('checked_at'))
        return (key != requested, seen if not recheck else not seen)
    return sorted(tasks, key=priority)


def dart_financial_task_plan(today=None):
    """Build a bounded, recent financial-data backfill plan from official DART periods."""
    today = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    current_year = today.year
    report_periods = []

    # Full statements cover the provider's available years, independently of
    # how many columns the initial screen shows. Newest reports remain first.
    for year in range(2015, current_year + 1):
        for report_code, (month, day) in DART_FINANCIAL_REPORT_CODES.items():
            if report_code == "11011":
                continue
            publish_year = year + (report_code == "11011")
            available_on = datetime(publish_year, month, day).date()
            if available_on <= today:
                report_periods.append((available_on, year, report_code))

    # The annual statement for the latest completed year is also part of the five-year view.
    for year in range(2015, current_year):
        available_on = datetime(year + 1, *DART_FINANCIAL_REPORT_CODES["11011"]).date()
        if available_on <= today:
            report_periods.append((available_on, year, "11011"))

    # OpenDART's single-company indicator endpoint starts with 2023 Q3 data.
    if current_year >= 2024:
        for report_code in ("11014", "11011"):
            report_year = 2024 if report_code == "11011" else 2023
            available_on = datetime(report_year, *DART_FINANCIAL_REPORT_CODES[report_code]).date()
            if available_on <= today:
                report_periods.append((available_on, 2023, report_code))

    report_periods = sorted(set(report_periods), reverse=True)
    tasks = []
    for available_on, year, report_code in report_periods:
        report_key = f"{year}:{report_code}"
        if year >= 2015:
            for basis in ("CFS", "OFS"):
                tasks.append(
                    {
                        "kind": "statement",
                        "year": str(year),
                        "report_code": report_code,
                        "basis": basis,
                        "report_key": report_key,
                        "query_key": f"statement:{report_key}:{basis}",
                        "available_on": available_on.isoformat(),
                    }
                )
        if year > 2023 or (year == 2023 and report_code in ("11014", "11011")):
            for category in DART_FINANCIAL_INDICATOR_CLASSES:
                tasks.append(
                    {
                        "kind": "indicator",
                        "year": str(year),
                        "report_code": report_code,
                        "category": category,
                        "report_key": report_key,
                        "query_key": f"indicator:{report_key}:{category}",
                        "available_on": available_on.isoformat(),
                    }
                )
    return tasks


def normalize_report_section(section, rows):
    """Keep useful company facts while avoiding fields irrelevant to investors."""
    if section == "shareholders":
        return [
            {
                "name": row.get("nm"),
                "relationship": row.get("relate"),
                "stock_type": row.get("stock_knd"),
                "beginning_shares": number(row.get("bsis_posesn_stock_co")),
                "beginning_ratio": number(row.get("bsis_posesn_stock_qota_rt")),
                "ending_shares": number(row.get("trmend_posesn_stock_co")),
                "ending_ratio": number(row.get("trmend_posesn_stock_qota_rt")),
                "as_of": row.get("stlm_dt"),
            }
            for row in rows
        ]
    if section == "dividends":
        return [
            {
                "category": row.get("se"),
                "stock_type": row.get("stock_knd"),
                "current": number(row.get("thstrm")),
                "previous": number(row.get("frmtrm")),
                "prior": number(row.get("lwfr")),
                "as_of": row.get("stlm_dt"),
            }
            for row in rows
        ]
    if section == "shares":
        return [
            {
                "stock_type": row.get("se"),
                "issued": number(row.get("istc_totqy")),
                "treasury": number(row.get("tesstk_co")),
                "circulating": number(row.get("distb_stock_co")),
                "as_of": row.get("stlm_dt"),
            }
            for row in rows
        ]
    if section == "employees":
        return [
            {
                "division": row.get("fo_bbm"),
                "gender": row.get("sexdstn"),
                "regular": number(row.get("rgllbr_co")),
                "contract": number(row.get("cnttk_co")),
                "total": number(row.get("sm")),
                "average_tenure": number(row.get("avrg_cnwk_sdytrn")),
                "average_salary": number(row.get("jan_salary_am")),
                "as_of": row.get("stlm_dt"),
            }
            for row in rows
        ]
    if section == "executives":
        return [
            {
                "name": row.get("nm"),
                "position": row.get("ofcps"),
                "registered": row.get("rgist_exctv_at"),
                "full_time": row.get("fte_at"),
                "responsibility": row.get("chrg_job"),
                "career": row.get("main_career"),
                "term_end": row.get("tenure_end_on"),
            }
            for row in rows
        ]
    if section == "minority_shareholders":
        return [
            {
                "category": row.get("se"),
                "holders": number(row.get("shrholdr_co")),
                "total_holders": number(row.get("shrholdr_tot_co")),
                "holder_ratio": number(row.get("shrholdr_rate")),
                "shares": number(row.get("hold_stock_co")),
                "total_shares": number(row.get("stock_tot_co")),
                "share_ratio": number(row.get("hold_stock_rate")),
                "as_of": row.get("stlm_dt"),
            }
            for row in rows
        ]
    if section == "holder_changes":
        return [
            {
                "date": row.get("change_on"),
                "name": row.get("mxmm_shrholdr_nm"),
                "shares": number(row.get("posesn_stock_co")),
                "ratio": number(row.get("qota_rt")),
                "cause": row.get("change_cause"),
                "note": row.get("rm"),
                "as_of": row.get("stlm_dt"),
            }
            for row in rows
        ]
    if section == "auditor":
        return [
            {
                "auditor": row.get("adtor"),
                "opinion": row.get("adt_opinion"),
                "special_matter": row.get("adt_reprt_spcmnt_matter"),
                "emphasis": row.get("emphs_matter"),
                "key_audit_matters": row.get("core_adt_matter"),
                "as_of": row.get("stlm_dt"),
            }
            for row in rows
        ]
    if section == "treasury":
        return [
            {
                "method": row.get("acqs_mth1"),
                "method_detail": row.get("acqs_mth2"),
                "method_type": row.get("acqs_mth3"),
                "stock_type": row.get("stock_knd"),
                "beginning": number(row.get("bsis_qy")),
                "acquired": number(row.get("change_qy_acqs")),
                "disposed": number(row.get("change_qy_dsps")),
                "cancelled": number(row.get("change_qy_incnr")),
                "ending": number(row.get("trmend_qy")),
                "note": row.get("rm"),
                "as_of": row.get("stlm_dt"),
            }
            for row in rows
        ]
    if section == "investments":
        return [
            {
                "company": row.get("inv_prm"),
                "first_acquisition_date": row.get("frst_acqs_de"),
                "purpose": row.get("invstmnt_purps"),
                "beginning_shares": number(row.get("bsis_blce_qy")),
                "beginning_ratio": number(row.get("bsis_blce_qota_rt")),
                "beginning_value": number(row.get("bsis_blce_acntbk_amount")),
                "change_shares": number(row.get("incrs_dcrs_acqs_dsps_qy")),
                "change_value": number(row.get("incrs_dcrs_acqs_dsps_amount")),
                "change_gain_loss": number(row.get("incrs_dcrs_evl_lstmn")),
                "ending_shares": number(row.get("trmend_blce_qy")),
                "ending_ratio": number(row.get("trmend_blce_qota_rt")),
                "ending_value": number(row.get("trmend_blce_acntbk_amount")),
                "assets": number(row.get("recent_bsns_year_fnnr_sttus_tot_assets")),
                "net_income": number(row.get("recent_bsns_year_fnnr_sttus_thstrm_ntpf")),
                "as_of": row.get("stlm_dt"),
            }
            for row in rows
        ]
    raise ValueError("Unsupported OpenDART report section")


def parse_members(output):
    members = {}
    for side, prefix, quantity in [
        ("buy", "shnu", "total_shnu_qty"),
        ("sell", "seln", "total_seln_qty"),
    ]:
        for rank in range(1, 6):
            name = output.get(f"{prefix}_mbcr_name{rank}", "").strip()
            if name:
                item = members.setdefault(name, {"name": name, "buy": None, "sell": None})
                item[side] = number(output.get(f"{quantity}{rank}"))
    return list(members.values())  # Absence from top five is not zero trading.


class AuthenticatedSources:
    def __init__(self, provider):
        self.provider, self.client, self.store = provider, provider.client, provider.store
        self.dart = os.getenv("DART_API_KEY", "")
        self.dart_lock = asyncio.Lock()
        self.corporations_lock = asyncio.Lock()
        self.financial_locks = {}

    async def _dart_get(self, url, *, params):
        if not self.dart:
            raise RuntimeError("OpenDART key is not configured")
        async with self.dart_lock:
            retry = self.store.get('dart-provider-retry', {})
            if retry.get('next_at', '') > now():
                raise DartProviderUnavailable('OpenDART provider maintenance retry pending')
            today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
            if self.store.get('dart-provider-limit',{}).get('date') == today:
                raise DartDailyBudgetExceeded('OpenDART provider request limit reached')
            usage = self.store.get("dart-usage", {})
            used = int(usage.get("count", 0)) if usage.get("date") == today else 0
            try:
                limit = int(os.getenv("DART_DAILY_REQUEST_BUDGET", "12000"))
            except ValueError:
                limit = 12000
            limit = max(100, min(20000, limit))
            if used >= limit:
                raise DartDailyBudgetExceeded("OpenDART daily safety budget reached")
            self.store.put("dart-usage", {"date": today, "count": used + 1, "limit": limit})
        response = await self.client.get(url, params=params)
        # The provider's limit response is account-wide. Do not retry it as a
        # separate company/period failure thousands of times until the day ends.
        status = None
        try:
            payload=response.json()
            if isinstance(payload,dict):status=str(payload.get('status') or '')
        except (ValueError,TypeError):
            try:
                if response.content.lstrip().lstrip(b'\xef\xbb\xbf').startswith(b'<'):
                    status=ET.fromstring(response.content).findtext('status')
            except (ET.ParseError,AttributeError):
                pass
        if status == '020':
            digest=self.store.raw('dart',url,response.content)
            self.store.put('dart-provider-limit',{'date':today,'status':'020',
                'received_at':now(),'source_hash':digest})
            raise DartDailyBudgetExceeded('OpenDART provider request limit reached')
        if status == '800':
            digest = self.store.raw('dart', url, response.content)
            self.store.put('dart-provider-retry', {'status': '800', 'received_at': now(),
                'source_hash': digest,
                'next_at': (datetime.now(UTC) + timedelta(minutes=15)).isoformat()})
            raise DartProviderUnavailable('OpenDART provider maintenance')
        return response

    @staticmethod
    def _age(received_at):
        try:
            value = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
            return max(0, (datetime.now(UTC) - value.astimezone(UTC)).total_seconds())
        except (AttributeError, TypeError, ValueError):
            return float("inf")

    @staticmethod
    def _financial_task_ttl(status):
        if status == "ready":
            return 180 * 86400
        if status == "no_data":
            return 30 * 86400
        if status == "error":
            return 6 * 3600
        return 0

    async def refresh_financial_data(self, code, corp_code=None, *, max_tasks=3):
        async with self.financial_locks.setdefault(code, asyncio.Lock()):
            return await self._refresh_financial_data(code, corp_code, max_tasks=max_tasks)

    async def _refresh_financial_data(self, code, corp_code=None, *, max_tasks=3):
        """Incrementally collect recent full statements and DART-reported ratios.

        Every query is independently checkpointed. A failed or empty response never
        replaces a previously successful report, and each response is retained in the
        append-only raw archive for later correction and audit.
        """
        if not self.dart:
            return {"status": "unavailable", "processed": 0}
        if not corp_code:
            corporations = self.store.get("dart-corporations", {}).get("codes", {})
            corp_code = corporations.get(code)
            if not corp_code:
                from .issuer_documents import company_link
                link=company_link(self.store,code,corporations)
                if link:
                    issuer=link['issuer_security_code']
                    result=await self.refresh_financial_data(issuer,corporations[issuer],max_tasks=max_tasks)
                    return {**result,'issuer_security_code':issuer,'scope':'company_financial_totals'}
        if not corp_code:
            return {"status": "unavailable", "processed": 0}

        official = self.store.get("official:" + code) or {"company": {}, "statements": []}
        progress = dict(official.get("financial_query_progress") or {})
        reports = dict(official.get("financial_reports") or {})
        indicators = dict(official.get("financial_indicators") or {})
        tasks = dart_financial_task_plan()
        due = []
        for task in tasks:
            checked = progress.get(task["query_key"], {})
            age = self._age(checked.get("checked_at"))
            if age >= self._financial_task_ttl(checked.get("status")):
                due.append(task)

        if not due:
            return {"status": "up_to_date", "processed": 0}

        requested = self.store.get('financial-request:' + code, {}).get('query_key')
        due = order_financial_tasks(due, progress, requested)

        processed = 0
        last_status = "ready"
        limit = max(1, min(12, int(max_tasks)))
        statement_url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
        indicator_url = "https://opendart.fss.or.kr/api/fnlttSinglIndx.json"
        period_names = {"11013": "1분기", "11012": "반기", "11014": "3분기", "11011": "연간"}

        for task in due[:limit]:
            params = {
                "crtfc_key": self.dart,
                "corp_code": corp_code,
                "bsns_year": task["year"],
                "reprt_code": task["report_code"],
            }
            url = statement_url if task["kind"] == "statement" else indicator_url
            if task["kind"] == "statement":
                params["fs_div"] = task["basis"]
            else:
                params["idx_cl_code"] = task["category"]

            try:
                response = await self._dart_get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                status = str(payload.get("status") or "")
                if status not in ("000", "013"):
                    raise ValueError("OpenDART financial response status " + (status or "missing"))

                safe_query = (
                    f"corp_code={corp_code}&bsns_year={task['year']}"
                    f"&reprt_code={task['report_code']}"
                )
                if task["kind"] == "statement":
                    safe_query += "&fs_div=" + task["basis"]
                else:
                    safe_query += "&idx_cl_code=" + task["category"]
                digest = self.store.raw("dart", url + "?" + safe_query, response.content)
                received_at = now()
                source_rows = payload.get("list") or []

                if task["kind"] == "statement":
                    identity = task["report_key"] + ":" + task["basis"]
                    old = reports.get(identity, {})
                    normalized = [
                        {
                            "name": row.get("account_nm"),
                            "account_id": row.get("account_id"),
                            "account_detail": row.get("account_detail"),
                            "statement": row.get("sj_div"),
                            "basis": task["basis"],
                            "period": task["year"],
                            "period_label": period_names[task["report_code"]],
                            "report_code": task["report_code"],
                            "reported_period": row.get("thstrm_nm"),
                            "current": number(row.get("thstrm_amount")),
                            "current_cumulative": number(row.get("thstrm_add_amount")),
                            "previous": number(row.get("frmtrm_amount")),
                            "previous_cumulative": number(row.get("frmtrm_add_amount")),
                            "previous_quarter": number(row.get("frmtrm_q_amount")),
                            "prior": number(row.get("bfefrmtrm_amount")),
                            "unit": row.get("currency", "KRW"),
                            "order": row.get("ord"),
                            "receipt": row.get("rcept_no"),
                            "source_trace": trace(
                                "opendart",
                                url + "?" + safe_query,
                                digest,
                                task["year"],
                                row.get("currency", "KRW"),
                                task["basis"] + " " + period_names[task["report_code"]],
                            ),
                        }
                        for row in source_rows
                    ]
                    if status == "000" and normalized:
                        # Queue exact filing IDs separately; XBRL collection never
                        # blocks a user's request or assumes a receipt is a time.
                        for receipt in {str(row.get("receipt") or "") for row in normalized}:
                            if len(receipt) == 14 and receipt.isdigit():
                                self.store.put("xbrl-request:"+receipt, {"code":code,"filing_id":receipt,
                                    "report_code":task["report_code"],"requested_at":now()})
                        snapshot_key = f"financial-report:{code}:{task['query_key']}:{digest}"
                        if self.store.get(snapshot_key) is None:
                            self.store.put(
                                snapshot_key,
                                {
                                    "year": task["year"],
                                    "report_code": task["report_code"],
                                    "report_name": period_names[task["report_code"]],
                                    "basis": task["basis"],
                                    "rows": normalized,
                                    "status": "ready",
                                    "received_at": received_at,
                                    "source_hash": digest,
                                },
                            )
                        prior_hashes = list(old.get("prior_hashes") or [])
                        if old.get("source_hash") and old.get("source_hash") != digest:
                            prior_hashes = list(dict.fromkeys(prior_hashes + [old["source_hash"]]))
                        reports[identity] = {
                            "year": task["year"],
                            "report_code": task["report_code"],
                            "report_name": period_names[task["report_code"]],
                            "basis": task["basis"],
                            "status": "ready",
                            "received_at": received_at,
                            "source_hash": digest,
                            "snapshot_key": snapshot_key,
                            "row_count": len(normalized),
                            "prior_hashes": prior_hashes,
                        }
                        last_status = "ready"
                    else:
                        if old.get("status") != "ready":
                            reports[identity] = {
                                "year": task["year"],
                                "report_code": task["report_code"],
                                "report_name": period_names[task["report_code"]],
                                "basis": task["basis"],
                                "status": "no_data",
                                "received_at": received_at,
                                "row_count": 0,
                            }
                        last_status = "no_data"
                else:
                    identity = task["report_key"] + ":" + task["category"]
                    old = indicators.get(identity, {})
                    normalized = [
                        {
                            "name": row.get("idx_nm"),
                            "code": row.get("idx_code"),
                            "category": row.get("idx_cl_nm"),
                            "category_code": task["category"],
                            "value": number(row.get("idx_val")),
                            "value_raw": row.get("idx_val"),
                            "unit": None,
                            "period": task["year"],
                            "period_label": period_names[task["report_code"]],
                            "report_code": task["report_code"],
                            "settlement_date": row.get("stlm_dt"),
                            "source_trace": trace(
                                "opendart",
                                url + "?" + safe_query,
                                digest,
                                row.get("stlm_dt") or task["year"],
                                "원문값",
                                row.get("idx_cl_nm") or task["category"],
                            ),
                        }
                        for row in source_rows
                    ]
                    if status == "000" and normalized:
                        snapshot_key = f"financial-report:{code}:{task['query_key']}:{digest}"
                        if self.store.get(snapshot_key) is None:
                            self.store.put(
                                snapshot_key,
                                {
                                    "year": task["year"],
                                    "report_code": task["report_code"],
                                    "report_name": period_names[task["report_code"]],
                                    "category_code": task["category"],
                                    "rows": normalized,
                                    "status": "ready",
                                    "received_at": received_at,
                                    "source_hash": digest,
                                },
                            )
                        prior_hashes = list(old.get("prior_hashes") or [])
                        if old.get("source_hash") and old.get("source_hash") != digest:
                            prior_hashes = list(dict.fromkeys(prior_hashes + [old["source_hash"]]))
                        indicators[identity] = {
                            "year": task["year"],
                            "report_code": task["report_code"],
                            "report_name": period_names[task["report_code"]],
                            "category_code": task["category"],
                            "status": "ready",
                            "received_at": received_at,
                            "source_hash": digest,
                            "snapshot_key": snapshot_key,
                            "row_count": len(normalized),
                            "prior_hashes": prior_hashes,
                        }
                        last_status = "ready"
                    else:
                        if old.get("status") != "ready":
                            indicators[identity] = {
                                "year": task["year"],
                                "report_code": task["report_code"],
                                "report_name": period_names[task["report_code"]],
                                "category_code": task["category"],
                                "status": "no_data",
                                "received_at": received_at,
                                "row_count": 0,
                            }
                        last_status = "no_data"

                progress[task["query_key"]] = {
                    "status": last_status,
                    "attempts": int(progress.get(task['query_key'],{}).get('attempts',0)) + 1,
                    "checked_at": received_at,
                    "row_count": len(source_rows),
                    "source_hash": digest,
                }
                processed += 1
                # Other coroutines may have persisted XBRL/dated accounts while
                # this HTTP request was in flight. Only replace our owned fields.
                official = self.store.get("official:" + code, {})
                official.update(
                    financial_reports=reports,
                    financial_indicators=indicators,
                    financial_query_progress=progress,
                    financial_data_received_at=received_at,
                )
                self.store.put("official:" + code, official)
            except DartDailyBudgetExceeded:
                return {"status": "rate_limited", "processed": processed}
            except Exception as exc:
                progress[task["query_key"]] = {
                    "status": "error",
                    "attempts": int(progress.get(task['query_key'],{}).get('attempts',0)) + 1,
                    "checked_at": now(),
                    "error": type(exc).__name__,
                }
                official = self.store.get("official:" + code, {})
                official["financial_query_progress"] = progress
                official.setdefault("financial_reports", reports)
                official.setdefault("financial_indicators", indicators)
                self.store.put("official:" + code, official)
                return {"status": "error", "processed": processed, "error": type(exc).__name__}

        return {"status": last_status, "processed": processed}

    async def corporations(self):
        """Return the daily stock-code to OpenDART corporation-code map."""
        from .dart_corporations import refresh_corporations
        async with self.corporations_lock:
            return await refresh_corporations(self)

    async def major_accounts_batch(self, listed_codes, *, year=None):
        """Backfill three-year annual major accounts in resumable groups of at most 100."""
        if not self.dart:
            return {"status": "unavailable", "processed": 0, "codes": {}}

        codes_by_stock = await self.corporations()
        eligible = sorted({str(code) for code in listed_codes if codes_by_stock.get(str(code))})
        if not eligible:
            return {"status": "no_data", "processed": 0, "codes": {}}

        fiscal_year = int(year or datetime.now(UTC).year - 1)
        fingerprint = hashlib.sha256(",".join(eligible).encode()).hexdigest()
        try:
            batch_size = int(os.getenv("DART_MAJOR_ACCOUNT_BATCH_SIZE", "100"))
        except ValueError:
            batch_size = 100
        batch_size = max(1, min(100, batch_size))
        try:
            refresh_days = int(os.getenv("DART_MAJOR_ACCOUNT_REFRESH_DAYS", "30"))
        except ValueError:
            refresh_days = 30
        refresh_days = max(1, min(365, refresh_days))

        progress = self.store.get("dart-major-accounts-progress", {})
        same_sweep = (
            progress.get("year") == fiscal_year and progress.get("code_fingerprint") == fingerprint
        )
        if same_sweep and progress.get("completed_at"):
            if self._age(progress["completed_at"]) < refresh_days * 86400:
                return {"status": "current", "processed": 0, "codes": {}}
        if same_sweep and progress.get("retry_after"):
            try:
                if datetime.fromisoformat(progress["retry_after"]) > datetime.now(UTC):
                    return {"status": "retry_wait", "processed": 0, "codes": {}}
            except ValueError:
                pass

        offset = int(progress.get("offset", 0)) if same_sweep else 0
        if offset >= len(eligible):
            offset = 0
        batch = eligible[offset : offset + batch_size]
        corp_codes = [codes_by_stock[code] for code in batch]
        endpoint = "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json"
        source_url = (
            endpoint
            + "?corp_code="
            + ",".join(corp_codes)
            + f"&bsns_year={fiscal_year}&reprt_code=11011"
        )
        params = {
            "crtfc_key": self.dart,
            "corp_code": ",".join(corp_codes),
            "bsns_year": str(fiscal_year),
            "reprt_code": "11011",
        }
        try:
            response = await self._dart_get(endpoint, params=params)
            response.raise_for_status()
            payload = response.json()
        except DartDailyBudgetExceeded:
            self.store.put(
                "dart-major-accounts-progress",
                {
                    "year": fiscal_year,
                    "code_fingerprint": fingerprint,
                    "offset": offset,
                    "retry_after": datetime.combine(
                        datetime.now(ZoneInfo("Asia/Seoul")).date() + timedelta(days=1),
                        datetime.min.time(),
                        tzinfo=ZoneInfo("Asia/Seoul"),
                    )
                    .astimezone(UTC)
                    .isoformat(),
                    "last_error": "daily_budget",
                    "updated_at": now(),
                },
            )
            return {"status": "rate_limited", "processed": 0, "codes": {}}
        except Exception as exc:
            self.store.put(
                "dart-major-accounts-progress",
                {
                    "year": fiscal_year,
                    "code_fingerprint": fingerprint,
                    "offset": offset,
                    "retry_after": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                    "last_error": type(exc).__name__,
                    "updated_at": now(),
                },
            )
            return {"status": "error", "processed": 0, "codes": {}}

        status = payload.get("status")
        if status not in ("000", "013"):
            delay = 3600 if status == "020" else 300
            self.store.put(
                "dart-major-accounts-progress",
                {
                    "year": fiscal_year,
                    "code_fingerprint": fingerprint,
                    "offset": offset,
                    "retry_after": (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(),
                    "last_error": str(status or "invalid_response"),
                    "updated_at": now(),
                },
            )
            return {"status": "error", "processed": 0, "codes": {}}

        digest = self.store.raw("dart", source_url, response.content)
        rows_by_stock = {code: [] for code in batch}
        for item in payload.get("list", []):
            code = str(item.get("stock_code") or "")
            if code not in rows_by_stock:
                continue
            statement = str(item.get("sj_div") or "")
            name = str(item.get("account_nm") or "").strip()
            if statement not in ("BS", "IS") or not name:
                continue
            currency = str(item.get("currency") or "KRW")
            rows_by_stock[code].append(
                {
                    "name": name,
                    "account_id": str(item.get("account_id") or f"{statement}:{name}"),
                    "statement": statement,
                    "basis": str(item.get("fs_div") or "CFS"),
                    "period": str(item.get("bsns_year") or fiscal_year),
                    "current": number(item.get("thstrm_amount")),
                    "previous": number(item.get("frmtrm_amount")),
                    "prior": number(item.get("bfefrmtrm_amount")),
                    "unit": currency,
                    "receipt": item.get("rcept_no"),
                    "source_trace": trace(
                        "opendart",
                        source_url,
                        digest,
                        str(item.get("bsns_year") or fiscal_year),
                        currency,
                        "다중회사 주요계정 · 사업보고서",
                    ),
                }
            )

        received_at = now()
        code_status = {}
        for code, incoming in rows_by_stock.items():
            official = self.store.get("official:" + code) or {"company": {}, "statements": []}
            statements = list(official.get("statements", []))
            full_periods = set(official.get("full_financials_periods", []))
            if not full_periods and official.get("financials_received_at"):
                full_periods = {
                    str(row.get("period"))
                    for row in statements
                    if row.get("period")
                    and "다중회사 주요계정"
                    not in str((row.get("source_trace") or {}).get("scope") or "")
                }
            statement_index = {
                (
                    row.get("basis"),
                    str(row.get("period")),
                    row.get("statement"),
                    row.get("name"),
                ): index
                for index, row in enumerate(statements)
            }
            full_exists = str(fiscal_year) in full_periods
            for row in incoming:
                identity = (
                    row["basis"],
                    row["period"],
                    row["statement"],
                    row["name"],
                )
                existing_index = statement_index.get(identity)
                if existing_index is None:
                    statement_index[identity] = len(statements)
                    statements.append(row)
                elif not full_exists:
                    prior_id = statements[existing_index].get("account_id")
                    statements[existing_index] = {
                        **row,
                        "account_id": prior_id or row["account_id"],
                    }
            if incoming:

                def statement_order(row):
                    try:
                        period_order = -int(row.get("period", 0))
                    except (TypeError, ValueError):
                        period_order = 0
                    basis_order = 0 if row.get("basis") == "CFS" else 1
                    statement_order = {"BS": 0, "IS": 1, "CIS": 2, "CF": 3}.get(
                        row.get("statement"), 4
                    )
                    return period_order, basis_order, statement_order

                statements.sort(key=statement_order)
                official.update(
                    statements=statements,
                    full_financials_periods=sorted(full_periods),
                    major_accounts_received_at=received_at,
                    major_accounts_period=str(fiscal_year),
                    major_accounts_status="ready",
                    major_accounts_scope="CFS/OFS 사업보고서 주요 재무상태·손익 계정",
                )
                self.store.put("official:" + code, official)
                code_status[code] = "ready"
            else:
                official.update(
                    major_accounts_received_at=received_at,
                    major_accounts_period=str(fiscal_year),
                    major_accounts_status="no_data",
                )
                self.store.put("official:" + code, official)
                code_status[code] = "no_data"

        next_offset = offset + len(batch)
        complete = next_offset >= len(eligible)
        self.store.put(
            "dart-major-accounts-progress",
            {
                "year": fiscal_year,
                "code_fingerprint": fingerprint,
                "total_companies": len(eligible),
                "batch_size": batch_size,
                "offset": 0 if complete else next_offset,
                "completed_at": received_at if complete else None,
                "last_batch_at": received_at,
                "last_status": status,
                "last_error": None,
                "updated_at": received_at,
            },
        )
        return {
            "status": "complete" if complete else "ready",
            "processed": len(batch),
            "year": fiscal_year,
            "codes": code_status,
        }

    async def periodic_report_facts(self, code):
        """Fetch one rotating annual-report section per company visit.

        OpenDART exposes these facts as separate endpoints. Keeping each section's
        status and refresh time lets the worker resume gradually across the full
        listed-company universe without making a burst of requests per company.
        """
        if not self.dart:
            return {"status": "unavailable", "section": None}

        budget_wait = self.store.get("dart-report-facts-retry-after", {})
        try:
            if budget_wait.get("retry_after") and datetime.fromisoformat(
                budget_wait["retry_after"]
            ) > datetime.now(UTC):
                return {"status": "rate_limited", "section": None}
        except (TypeError, ValueError):
            pass

        corporations = await self.corporations()
        corp = corporations.get(code)
        if not corp:
            return {"status": "no_data", "section": None}

        year = str(datetime.now(ZoneInfo("Asia/Seoul")).year - 1)
        report_code = "11011"
        endpoint_by_section = {
            "shareholders": "hyslrSttus.json",
            "dividends": "alotMatter.json",
            "shares": "stockTotqySttus.json",
            "employees": "empSttus.json",
            "executives": "exctvSttus.json",
            "minority_shareholders": "mrhlSttus.json",
            "holder_changes": "hyslrChgSttus.json",
            "auditor": "accnutAdtorNmNdAdtOpinion.json",
            "treasury": "tesstkAcqsDspsSttus.json",
            "investments": "otrCprInvstmntSttus.json",
        }
        official = self.store.get("official:" + code) or {"company": {}, "statements": []}
        report_facts = official.get("report_facts") or {}
        sections = dict(report_facts.get("sections") or {})
        try:
            refresh_days = int(os.getenv("DART_REPORT_FACT_REFRESH_DAYS", "30"))
        except ValueError:
            refresh_days = 30
        refresh_days = max(7, min(365, refresh_days))
        now_dt = datetime.now(UTC)
        candidates = []
        for section in DART_REPORT_SECTIONS:
            current = sections.get(section) or {}
            checked_year = current.get("checked_year") or current.get("year")
            if checked_year != year:
                candidates.append(section)
                continue
            retry_after = current.get("retry_after")
            try:
                if retry_after and datetime.fromisoformat(retry_after) > now_dt:
                    continue
            except ValueError:
                pass
            checked_status = current.get("checked_status") or current.get("status")
            ttl_days = 90 if checked_status == "no_data" else refresh_days
            checked_at = current.get("checked_at") or current.get("received_at")
            if self._age(checked_at) >= ttl_days * 86400:
                candidates.append(section)

        if not candidates:
            return {"status": "current", "section": None}

        cursor = int(report_facts.get("cursor", 0)) % len(DART_REPORT_SECTIONS)
        selected = next(
            DART_REPORT_SECTIONS[(cursor + step) % len(DART_REPORT_SECTIONS)]
            for step in range(len(DART_REPORT_SECTIONS))
            if DART_REPORT_SECTIONS[(cursor + step) % len(DART_REPORT_SECTIONS)] in candidates
        )
        url = "https://opendart.fss.or.kr/api/" + endpoint_by_section[selected]
        params = {
            "crtfc_key": self.dart,
            "corp_code": corp,
            "bsns_year": year,
            "reprt_code": report_code,
        }
        try:
            response = await self._dart_get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            status = payload.get("status")
            if status not in ("000", "013"):
                retry_minutes = 360 if status == "020" else 60
                previous = sections.get(selected) or {}
                sections[selected] = {
                    **previous,
                    "checked_year": year,
                    "checked_at": now(),
                    "checked_status": "error",
                    **(
                        {"status": "error", "year": year, "rows": []}
                        if not previous.get("rows")
                        else {}
                    ),
                    "retry_after": (now_dt + timedelta(minutes=retry_minutes)).isoformat(),
                    "last_error": str(status or "invalid_response"),
                }
                report_facts.update(
                    year=year,
                    report_code=report_code,
                    sections=sections,
                    cursor=(DART_REPORT_SECTIONS.index(selected) + 1) % len(DART_REPORT_SECTIONS),
                )
                official = self.store.get("official:" + code, {})
                official["report_facts"] = report_facts
                self.store.put("official:" + code, official)
                return {"status": "error", "section": selected}
        except DartDailyBudgetExceeded:
            retry_after = (
                datetime.combine(
                    datetime.now(ZoneInfo("Asia/Seoul")).date() + timedelta(days=1),
                    datetime.min.time(),
                    tzinfo=ZoneInfo("Asia/Seoul"),
                )
                .astimezone(UTC)
                .isoformat()
            )
            self.store.put(
                "dart-report-facts-retry-after",
                {"retry_after": retry_after, "updated_at": now()},
            )
            return {"status": "rate_limited", "section": selected}
        except Exception as exc:
            previous = sections.get(selected) or {}
            sections[selected] = {
                **previous,
                "checked_year": year,
                "checked_at": now(),
                "checked_status": "error",
                **(
                    {"status": "error", "year": year, "rows": []}
                    if not previous.get("rows")
                    else {}
                ),
                "retry_after": (now_dt + timedelta(minutes=60)).isoformat(),
                "last_error": type(exc).__name__,
            }
            report_facts.update(
                year=year,
                report_code=report_code,
                sections=sections,
                cursor=(DART_REPORT_SECTIONS.index(selected) + 1) % len(DART_REPORT_SECTIONS),
            )
            official = self.store.get("official:" + code, {})
            official["report_facts"] = report_facts
            self.store.put("official:" + code, official)
            raise

        rows = payload.get("list", []) if status == "000" else []
        if not isinstance(rows, list):
            raise ValueError("OpenDART company report returned an invalid list")
        digest = self.store.raw(
            "dart",
            url + f"?corp_code={corp}&bsns_year={year}&reprt_code={report_code}",
            response.content,
        )
        received_at = now()
        normalized = normalize_report_section(selected, rows)
        previous = sections.get(selected) or {}
        if normalized:
            sections[selected] = {
                "status": "ready",
                "year": year,
                "checked_year": year,
                "checked_status": "ready",
                "rows": normalized,
                "received_at": received_at,
                "checked_at": received_at,
                "as_of": next((row.get("stlm_dt") for row in rows if row.get("stlm_dt")), None),
                "source_trace": trace(
                    "opendart",
                    url + f"?corp_code={corp}&bsns_year={year}&reprt_code={report_code}",
                    digest,
                    year,
                    "공시 값",
                    "사업보고서 주요정보",
                ),
            }
        elif previous.get("rows"):
            # Keep the last available annual report while a newer report has no data yet.
            sections[selected] = {
                **previous,
                "checked_year": year,
                "checked_status": "no_data",
                "checked_at": received_at,
            }
        else:
            sections[selected] = {
                "status": "no_data",
                "year": year,
                "checked_year": year,
                "checked_status": "no_data",
                "rows": [],
                "received_at": received_at,
                "checked_at": received_at,
                "source_trace": trace(
                    "opendart",
                    url + f"?corp_code={corp}&bsns_year={year}&reprt_code={report_code}",
                    digest,
                    year,
                    "공시 값",
                    "사업보고서 주요정보",
                ),
            }
        report_facts.update(
            year=year,
            report_code=report_code,
            sections=sections,
            cursor=(DART_REPORT_SECTIONS.index(selected) + 1) % len(DART_REPORT_SECTIONS),
        )
        official = self.store.get("official:" + code, {})
        official["report_facts"] = report_facts
        self.store.put("official:" + code, official)
        return {
            "status": sections[selected].get("checked_status", sections[selected]["status"]),
            "section": selected,
        }

    async def filings(self, code):
        if not self.dart:
            return
        corporations = await self.corporations()
        from .issuer_documents import document_issuer, project_documents
        issuer = document_issuer(self.store, code, corporations)
        corp = corporations.get(issuer)
        if issuer and issuer != code:
            # Company-wide disclosures are shared, never per-share data. Reuse
            # a fresh issuer feed instead of issuing four identical DART queries.
            existing = self.store.get('filings:' + issuer, {})
            project_documents(self.store, code, issuer)
            if existing.get('status') != 'ready' or self._age(existing.get('received_at')) >= 900:
                await self.filings(issuer)
                project_documents(self.store, code, issuer)
            return
        if not corp:
            self.store.put(
                "filings:" + code,
                {"rows": [], "status": "unavailable", "received_at": now()},
            )
            return
        official = self.store.get("official:" + code) or {"company": {}, "statements": []}
        if (
            not official.get("company")
            or self._age(official.get("company_received_at")) > 30 * 86400
        ):
            company_url = "https://opendart.fss.or.kr/api/company.json"
            company_response = await self._dart_get(
                company_url, params={"crtfc_key": self.dart, "corp_code": corp}
            )
            company = company_response.json()
            if company.get("status") == "000":
                digest = self.store.raw(
                    "dart", company_url + "?corp_code=" + corp, json.dumps(company).encode()
                )
                official = self.store.get("official:" + code, {})
                official.update(
                    {
                        "company": {
                            k: company.get(k)
                            for k in (
                                "corp_name",
                                "corp_name_eng",
                                "ceo_nm",
                                "hm_url",
                                "est_dt",
                                "induty_code",
                                "acc_mt",
                            )
                        },
                        "source_trace": trace(
                            "opendart", company_url, digest, now()[:10], "기업정보"
                        ),
                        "company_received_at": now(),
                    }
                )
                self.store.put("official:" + code, official)

        from .disclosures import collect_filings
        await collect_filings(self, code, corp)

    async def market_filings(self):
        """Cache a compact market-wide OpenDART feed for the public home screen."""
        if not self.dart:
            return None

        today = datetime.now(ZoneInfo("Asia/Seoul")).date()
        start = today - timedelta(days=14)
        url = "https://opendart.fss.or.kr/api/list.json"
        query = {
            "crtfc_key": self.dart,
            "bgn_de": start.strftime("%Y%m%d"),
            "end_de": today.strftime("%Y%m%d"),
            "page_no": "1",
            "page_count": "100",
            "sort": "date",
            "sort_mth": "desc",
            "last_reprt_at": "N",
        }
        rows = []
        seen = set()
        sources = []
        # DART corp_cls is single-valued. A combined Y,K query is not supported.
        # Publish only after both calls succeed so a failure cannot replace a
        # complete last-good snapshot with one market's partial list.
        for corp_cls in ("Y", "K"):
            params = {**query, "corp_cls": corp_cls}
            source_url = url + "?" + urlencode({k: v for k, v in params.items() if k != "crtfc_key"})
            response = await self._dart_get(url, params=params)
            digest = self.store.raw("dart", source_url, response.content)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("status") not in ("000", "013"):
                status = payload.get("status") if isinstance(payload, dict) else "invalid_format"
                raise ValueError("DART market feed unavailable: " + str(status))
            items = payload.get("list") if payload["status"] == "000" else []
            if not isinstance(items, list):
                raise ValueError("DART market feed has an invalid list")
            sources.append({
                "corp_cls": corp_cls, "url": source_url, "source_hash": digest,
                "status": payload["status"], "received_count": len(items),
                "total_count": int(payload.get("total_count", len(items))),
            })
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("DART market feed has an invalid row")
                receipt = str(item.get("rcept_no") or "")
                report_date = str(item.get("rcept_dt") or "")
                if len(receipt) != 14 or not receipt.isdecimal() or len(report_date) != 8:
                    raise ValueError("DART market feed has an invalid receipt/date")
                datetime.strptime(report_date, "%Y%m%d")
                if receipt in seen:
                    continue
                seen.add(receipt)
                if item.get("corp_cls", corp_cls) not in ("Y", "K"):
                    continue
                rows.append({
                    "corp_name": item.get("corp_name") or "회사명 미제공",
                    "code": item.get("stock_code"), "corp_code": item.get("corp_code"),
                    "corp_cls": item.get("corp_cls") or corp_cls,
                    "title": item.get("report_nm") or "공시 제목 미제공",
                    "date": report_date, "receipt": receipt,
                    "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt,
                    "source_trace": trace("opendart", source_url, digest, report_date,
                        unit="공시", scope="최근 14일 · 코스피·코스닥 · 정정 공시 포함"),
                })

        # Store a compact manifest linking both immutable response bodies.
        digest = self.store.raw(
            "dart", url + "#listed-market-merge",
            json.dumps({"sources": sources}, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        )
        rows.sort(key=lambda row: (row["date"], row["receipt"]), reverse=True)

        snapshot = {
            "rows": rows,
            "status": "ready" if rows else "no_data",
            "received_at": now(),
            "total": sum(source["total_count"] for source in sources),
            "sources": sources,
            "scope": "최근 14일 · 최신 100건 · 정정 공시 포함",
            "source_trace": trace(
                "opendart",
                url,
                digest,
                f"{start.isoformat()}–{today.isoformat()}",
                unit="공시",
                scope="최근 14일 · 코스피·코스닥별 첫 100건 병합",
                formula="deduplicate receipt; sort date and receipt descending; filter eligible issuers; latest 100",
            ),
        }
        from .filing_scope import public_filings
        snapshot = public_filings(self.store, snapshot)
        snapshot["rows"] = snapshot["rows"][:100]
        snapshot["displayed_count"] = len(snapshot["rows"])
        snapshot["status"] = "ready" if snapshot["rows"] else "no_data"
        self.store.put("market-filings", snapshot)
        return snapshot

