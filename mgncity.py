"""Shared code for submitting meter readings to mgn-city.ru ("Передача показаний приборов учета").

wmpost.py (water), elpost.py (electricity) and gaspost.py (gas) are thin wrappers
around main(site).
Each run repeats what the browser does:
  1. GET  the site's account lookup page          -> cookies + antiforgery token
  2. POST /TransferReadings/Counters              -> table of the account's meters
  3. POST the site's submit URL (AJAX)            -> result JSON

Only the Python standard library is used (Python 3.9+).
"""

import argparse
import base64
import html
import http.client
import http.cookiejar
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

BASE_URL = "https://mgn-city.ru"
COUNTERS_URL = BASE_URL + "/TransferReadings/Counters"
USER_AGENT = "Mozilla/5.0 (compatible; mgn-readings-post/1.0)"
TIMEOUT = 30
LOOKUP_ATTEMPTS = 3


class SiteError(Exception):
    pass


class HTTPStatusError(SiteError):
    def __init__(self, status, reason, url):
        super().__init__(f"HTTP {status} {reason} from {url}")
        self.status = status


class PageParser(HTMLParser):
    """Collects every <input> (tagged with the id of its enclosing form) and the
    dates shown in the "last reading" labels."""

    def __init__(self):
        super().__init__()
        self.inputs = []   # attribute dicts in document order; "_form" is the enclosing form id
        self.dates = {}    # data-id -> date of last reading
        self._form = None
        self._label_id = None

    @classmethod
    def parse(cls, page):
        p = cls()
        p.feed(page)
        p.close()
        return p

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._form = a.get("id") or ""
        elif tag == "input":
            a["_form"] = self._form
            self.inputs.append(a)
        elif tag == "label" and has_class(a, "last-indication-table"):
            self._label_id = a.get("data-id")

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "label":
            self._label_id = None

    def handle_data(self, data):
        if self._label_id:
            m = re.search(r"\d{2}\.\d{2}\.\d{4}", data)
            if m:
                self.dates[self._label_id] = m.group(0)


class Client:
    """HTTPS client that keeps one connection open (like a browser) and stores cookies.

    urllib opens a new connection per request with "Connection: close", and with
    that this site intermittently cuts off larger pages (IncompleteRead).
    Reusing a keep-alive connection avoids it.
    """

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.conn = None

    def _connect(self):
        host = urllib.parse.urlsplit(BASE_URL).hostname
        proxy = urllib.request.getproxies().get("https")
        if not proxy or urllib.request.proxy_bypass(host):
            return http.client.HTTPSConnection(host, timeout=TIMEOUT)
        p = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
        tunnel_headers = {}
        if p.username:
            creds = f"{urllib.parse.unquote(p.username)}:{urllib.parse.unquote(p.password or '')}"
            tunnel_headers["Proxy-Authorization"] = "Basic " + base64.b64encode(creds.encode()).decode()
        conn = http.client.HTTPSConnection(p.hostname, p.port, timeout=TIMEOUT)
        conn.set_tunnel(host, 443, headers=tunnel_headers)
        return conn

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _send(self, url, data, headers):
        method = "GET" if data is None else "POST"
        body = None if data is None else urllib.parse.urlencode(data).encode()
        for _ in range(5):
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
            if body is not None and not req.has_header("Content-type"):
                req.add_header("Content-type", "application/x-www-form-urlencoded")
            self.jar.add_cookie_header(req)
            if self.conn is None:
                self.conn = self._connect()
            target = urllib.parse.urlsplit(url)
            path = target.path + ("?" + target.query if target.query else "")
            try:
                self.conn.request(method, path, body=body, headers=dict(req.header_items()))
                resp = self.conn.getresponse()
                payload = resp.read()
            except BaseException:
                self.close()
                raise
            self.jar.extract_cookies(resp, req)
            location = resp.getheader("Location")
            if resp.status in (301, 302, 303, 307, 308) and location:
                url = urllib.parse.urljoin(url, location)
                if resp.status in (301, 302, 303):
                    method, body = "GET", None
                    headers = {k: v for k, v in (headers or {}).items() if k.lower() != "content-type"}
                continue
            if resp.status >= 400:
                raise HTTPStatusError(resp.status, resp.reason, url)
            charset = resp.headers.get_content_charset() or "utf-8"
            return url, payload.decode(charset, "replace")
        raise SiteError(f"too many redirects, last one to {url}")

    def request(self, url, data=None, headers=None, attempts=1):
        """POSTs `data` (list of pairs) or GETs if it is None.

        Returns (final url after redirects, decoded body). Network errors and 5xx
        responses are retried up to `attempts` times; only pass attempts > 1 for
        requests that are safe to repeat.
        """
        for attempt in range(1, attempts + 1):
            try:
                return self._send(url, data, headers)
            except HTTPStatusError as e:
                error = e
                if e.status < 500:
                    break
            except (OSError, http.client.HTTPException) as e:
                error = SiteError(f"network error talking to {url}: {getattr(e, 'reason', None) or e}")
            if attempt < attempts:
                time.sleep(2 * attempt)
        raise error


def has_class(attrs, name):
    return name in (attrs.get("class") or "").split()


def successful_inputs(inputs, form_id):
    """(input attrs, value) for each input of a form that the browser (jQuery .serialize()) sends."""
    result = []
    for a in inputs:
        if a["_form"] != form_id or not a.get("name") or "disabled" in a:
            continue
        kind = (a.get("type") or "text").lower()
        if kind in ("submit", "button", "image", "reset", "file"):
            continue
        if kind in ("checkbox", "radio"):
            if "checked" not in a:
                continue
            result.append((a, a.get("value") or "on"))
        else:
            result.append((a, a.get("value") or ""))
    return result


def form_fields(inputs, form_id):
    return [(a["name"], value) for a, value in successful_inputs(inputs, form_id)]


def normalize_number(text):
    return re.sub(r"[\s№]", "", text).upper()


def to_decimal(text):
    try:
        return Decimal(text.strip().replace(",", "."))
    except (AttributeError, InvalidOperation):
        return None


def strip_html(text):
    text = re.sub(r"<br\s*/?>|</p>|</div>|</h\d>", "\n", text or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def parse_reading(text, what="reading"):
    reading = to_decimal(text)
    if reading is None or not reading.is_finite():
        raise argparse.ArgumentTypeError(f"{what} is not a number: {text!r}")
    if reading < 0 or reading.as_tuple().exponent < -3:
        raise argparse.ArgumentTypeError(f"{what} must be >= 0 with at most 3 decimals: {text!r}")
    return reading


def parse_meter_arg(text):
    number, sep, value = text.partition("=")
    number = normalize_number(number)
    if not sep or not number or not value.strip():
        raise argparse.ArgumentTypeError(f"expected METER=VALUE, got {text!r}")
    return number, parse_reading(value, f"reading for {number}")


def open_counters_page(client, account_url, account):
    _, page = client.request(account_url, attempts=LOOKUP_ATTEMPTS)
    fields = form_fields(PageParser.parse(page).inputs, "getAccountForm")
    if "AccountNumberValue" not in dict(fields):
        raise SiteError(f"account form not found on {account_url} (site layout changed?)")
    fields = [(n, account if n == "AccountNumberValue" else v) for n, v in fields]

    url, page = client.request(COUNTERS_URL, fields, {"Referer": account_url}, attempts=LOOKUP_ATTEMPTS)
    parsed = urllib.parse.urlparse(url)
    if parsed.path.rstrip("/").endswith("/Account"):
        # The site redirects back to the lookup form with ?message=...&result=2
        msg = urllib.parse.parse_qs(parsed.query).get("message", ["account lookup rejected"])[0]
        raise SiteError(f"account {account}: {msg}")
    return page


def find_meters(parsed, number_attr):
    """Meters from the readings table: one per "Новое показание" input."""
    def by_id(cls):
        return {a["data-id"]: a for a in parsed.inputs if has_class(a, cls) and a.get("data-id")}

    last, sums = by_id("last-indication"), by_id("sum-indication")
    meters = []
    for a in parsed.inputs:
        if not (has_class(a, "new-indication") and a.get("data-id")):
            continue
        cid = a["data-id"]
        s = sums.get(cid)
        meters.append({
            "id": cid,
            "number": a.get(number_attr) or "",
            "service": a.get("data-service") or "",
            "attrs": a,
            "last": to_decimal(last.get(cid, {}).get("value")),
            "last_date": parsed.dates.get(cid, ""),
            "sum_input": s,
            "max_sum": to_decimal((s or {}).get("data-rule-max")),
            "warning": to_decimal((s or {}).get("data-warning")),
        })
    return meters


def check_reading(meter, reading):
    """Returns problems the site would flag (or that look like typos)."""
    if meter["last"] is None:
        return []
    usage = reading - meter["last"]
    problems = []
    if usage < 0:
        problems.append(f"new reading {reading} is lower than last reading {meter['last']}")
    if meter["max_sum"] is not None and usage > meter["max_sum"]:
        problems.append(f"consumption {usage} exceeds the site's limit of {meter['max_sum']}")
    if meter["warning"] and usage > meter["warning"]:
        problems.append(f"consumption {usage} is above the site's warning threshold {meter['warning']}")
    return problems


class WaterSite:
    """Water meters (system=SN)."""

    description = "Submit water meter readings to mgn-city.ru"
    example = "25-1111222=42"
    account_url = BASE_URL + "/TransferReadings/Account?system=SN"
    submit_url = BASE_URL + "/TransferReadings/SetCounterIndication"
    number_attr = "data-number"
    meter_ids = True

    def build_payload(self, page, parsed, meters, values):
        """Fields exactly as jQuery's $.param encodes the browser's {model: ...} object."""
        # The submit handler has these baked into the page's JavaScript.
        acc = re.search(r"AccountNumber:\s*'([^']*)'", page)
        base = re.search(r"BaseId:\s*'([^']*)'", page)
        if not acc or not base:
            raise SiteError("AccountNumber/BaseId not found on the readings page (site layout changed?)")
        data = []
        for i, m in enumerate(meters):
            key = f"model[CounterIndicationList][{i}]"
            data += [
                (key + "[counterId]", m["id"]),
                (key + "[serviceTypeName]", m["service"]),
                (key + "[counterNumber]", m["number"]),
                (key + "[counterName]", m["attrs"].get("data-name") or ""),
                (key + "[val]", values.get(m["id"], "")),
            ]
        return data + [("model[AccountNumber]", acc.group(1)), ("model[BaseId]", base.group(1))]

    def report(self, data, meters, values):
        """Prints the site's answer; returns True if every submitted reading was accepted."""
        if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
            raise SiteError("unexpected response from the site: " + json.dumps(data, ensure_ascii=False)[:500])
        numbers = {m["id"]: m["number"] for m in meters}
        for r in data:
            cid = str(r.get("counterId", ""))
            status = "OK" if r.get("code") == 0 else f"FAILED (code {r.get('code')})"
            print(f"  № {numbers.get(cid, cid)}: {status}")
            msg = strip_html(r.get("messageHTML"))
            if msg:
                print("    " + msg.replace("\n", "\n    "))
        relevant = [r for r in data if str(r.get("counterId", "")) in values] or data
        return all(r.get("code") == 0 for r in relevant)


class SupplierSite:
    """Readings form of a supplier (system=PS), used for electricity and gas."""

    submit_url = BASE_URL + "/TransferReadings/SetCounterIndicationPS"
    number_attr = "data-name"
    form_id = "indicationPSForm"

    def __init__(self, description, account_url, example, meter_ids=True):
        self.description = description
        self.account_url = account_url
        self.example = example
        # False: the meter has no number on the site; -meter is just the value for the first meter.
        self.meter_ids = meter_ids

    def build_payload(self, page, parsed, meters, values):
        """The whole readings form as jQuery's .serialize() sends it, with readings filled in.

        Inputs are filled by identity, not by name: the gas form also has a hidden
        input with the same name as the visible reading field, which the browser
        sends empty.
        """
        filled = {}  # id() of an input's attribute dict -> value to send
        for m in meters:
            if not m["attrs"].get("name"):
                raise SiteError(f"reading field of meter {m['number']} has no name (site layout changed?)")
            val = values.get(m["id"], "")
            filled[id(m["attrs"])] = val
            if m["sum_input"] is not None:
                # Read-only "Расход" field that the page's JavaScript fills in as new - last.
                usage = "" if not val or m["last"] is None else format((Decimal(val) - m["last"]).normalize(), "f")
                filled[id(m["sum_input"])] = usage
        inputs = successful_inputs(parsed.inputs, self.form_id)
        if not inputs:
            raise SiteError("readings form not found on the page (site layout changed?)")
        return [(a["name"], filled.get(id(a), value)) for a, value in inputs]

    def report(self, data, meters, values):
        """Prints the site's answer; returns True if the readings were accepted."""
        if not isinstance(data, dict) or "result" not in data:
            raise SiteError("unexpected response from the site: " + json.dumps(data, ensure_ascii=False)[:500])
        ok = data["result"] == 0
        print("  " + ("OK" if ok else f"FAILED (result {data['result']})"))
        msg = strip_html(data.get("message"))
        if msg:
            print("    " + msg.replace("\n", "\n    "))
        for s in (data.get("subServices") or []) if ok else []:
            if not isinstance(s, dict) or not s.get("hasMeter"):
                continue
            try:
                last = s["meterInfo"][0]["lastIndications"][0]
                value = last["values"][0]["value"]
                date = ".".join(reversed(str(last.get("date") or "").split("T")[0].split("-")))
            except (KeyError, IndexError, TypeError):
                continue
            print(f"  № {s.get('subServiceName')}: the site now shows last reading {value} ({date})")
        return ok


WATER = WaterSite()
ELECTRICITY = SupplierSite(
    "Submit electricity meter readings (ООО «МЭК») to mgn-city.ru",
    BASE_URL + "/TransferReadings/Account?system=PS&serviceCode=147&providerId=5&groupId=15",
    "011111111111110000=17650")
GAS = SupplierSite(
    "Submit gas meter readings (ООО «НОВАТЭК-Челябинск») to mgn-city.ru",
    BASE_URL + "/TransferReadings/Account?system=PS&serviceCode=4&providerId=6&groupId=1",
    "15", meter_ids=False)


def main(site):
    ap = argparse.ArgumentParser(description=site.description)
    ap.add_argument("-account", required=True, help="personal account number (лицевой счет)")
    if site.meter_ids:
        ap.add_argument("-meter", required=True, action="append", type=parse_meter_arg, metavar="NUMBER=VALUE",
                        help=f'meter number and new reading, e.g. "{site.example}"; repeat for each meter')
    else:
        ap.add_argument("-meter", required=True, type=parse_reading, metavar="VALUE",
                        help=f'new reading, e.g. {site.example}; it goes into the first "Новое показание" field')
    ap.add_argument("--dry-run", action="store_true", help="look up meters and show what would be sent, but do not submit")
    ap.add_argument("--force", action="store_true", help="submit even if a reading fails the sanity checks")
    args = ap.parse_args()

    account = args.account.strip()
    wanted = {}
    for number, reading in args.meter if site.meter_ids else []:
        if number in wanted:
            ap.error(f"meter {number} given more than once")
        wanted[number] = reading

    client = Client()
    try:
        page = open_counters_page(client, site.account_url, account)
        parsed = PageParser.parse(page)
        meters = find_meters(parsed, site.number_attr)
        if not meters:
            # e.g. the electricity site shows <h3 class="no-data-msg">Лицевой счет не найден</h3>
            msg = re.search(r'class="no-data-msg"[^>]*>(.*?)<', page, re.S)
            raise SiteError(f"account {account}: " + (strip_html(msg.group(1)) if msg else "no meters found"))
    except SiteError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    address = re.search(r"Адрес:\s*<b>(.*?)</b>", page, re.S)
    print(f"Account {account}" + (f" — {html.unescape(address.group(1)).strip()}" if address else ""))

    if site.meter_ids:
        # Several reading fields with one meter number (e.g. day/night tariffs) can't be
        # told apart from the command line, so such meters are skipped.
        counts = Counter(normalize_number(m["number"]) for m in meters)
        assigned, ambiguous = {}, set()
        for m in meters:
            number = normalize_number(m["number"])
            if number not in wanted:
                continue
            if counts[number] > 1:
                ambiguous.add(m["id"])
            else:
                assigned[m["id"]] = wanted[number]
        not_found = [n for n in wanted if n not in counts]
    else:
        assigned, ambiguous, not_found = {meters[0]["id"]: args.meter}, set(), []

    num_width = max(len(n) for n in [m["number"] for m in meters] + not_found)
    svc_width = max(len(m["service"]) for m in meters)
    values, problems = {}, []
    for m in meters:
        last = "—" if m["last"] is None else f"{m['last']}" + (f" ({m['last_date']})" if m["last_date"] else "")
        line = f"  № {m['number']:<{num_width}}" + (f"  {m['service']:<{svc_width}}" if svc_width else "") + f"  last {last}"
        reading = assigned.get(m["id"])
        if m["id"] in ambiguous:
            print(line + "   (several reading fields for this meter, not supported, skipped)")
        elif reading is None:
            print(line + "   (not submitted)")
        else:
            values[m["id"]] = format(reading, "f")
            usage = "" if m["last"] is None else f"   consumption {reading - m['last']}"
            print(line + f"   new {values[m['id']]}{usage}")
            problems += [f"№ {m['number']}: {p}" for p in check_reading(m, reading)]
    for number in not_found:
        print(f"  № {number:<{num_width}}  not found on this account, skipped")

    if not values:
        print("No matching meters, nothing to submit.")
        return 1

    if problems:
        sys.stdout.flush()
        for p in problems:
            print(("warning: " if args.force else "error: ") + p, file=sys.stderr)
        if not args.force:
            print("nothing submitted; fix the readings or rerun with --force", file=sys.stderr)
            return 1

    try:
        payload = site.build_payload(page, parsed, meters, values)
    except SiteError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"\nDry run, not submitting. Would POST to {site.submit_url}:")
        for k, v in payload:
            print(f"  {k} = {v}")
        return 0

    try:
        _, body = client.request(site.submit_url, payload, {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": BASE_URL,
            "Referer": COUNTERS_URL,
        })
    except SiteError as e:
        sys.stdout.flush()
        print(f"error: submit failed: {e}\n"
              "The site may or may not have recorded the readings; check them on the site before retrying.",
              file=sys.stderr)
        return 1

    print("\nResult:")
    try:
        ok = site.report(json.loads(body), meters, values)
    except ValueError:
        ok = None
        error = "unexpected response from the site:\n" + strip_html(body)[:500]
    except SiteError as e:
        ok = None
        error = str(e)
    if ok is None:
        sys.stdout.flush()
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0 if ok else 1
