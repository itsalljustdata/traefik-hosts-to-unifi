#!/usr/bin/env python3
"""
Script to retrieve all HTTP routers with Host() rules from Traefik API
"""

import json
import argparse
import logging
import os
import re
from typing import Dict, List
import socket
import ssl
from pathlib import Path
import tldextract


class MalleableLogger(logging.Logger):

    def __init__(self, name):
        super().__init__(name)

    def _log_with_force(self, level, msg, *args, force: bool = False, **kwargs):
        """Log a message with the given level, bypassing handler level filters."""
        if force:
            logger_level = self.getEffectiveLevel()
            handler_levels = [(h, h.level) for h in self.handlers]
            try:
                self.setLevel(level)
                for h in self.handlers:
                    h.setLevel(level)
                self.log(level, msg=msg, *args, **kwargs)
            finally:
                for h, h_level in handler_levels:
                    h.setLevel(h_level)
                self.setLevel(logger_level)
        else:
            self.log(level, msg, *args, **kwargs)

def _install_level_methods() -> None:
    def _make_method(level_value: int):
        def _level_method(self, msg, *args, force: bool = False, **kwargs):
            self._log_with_force(level_value, msg, *args, force=force, **kwargs)

        return _level_method

    for name, val  in logging._nameToLevel.items():
        if not isinstance(val, int) or val == 0:
            continue
        setattr(MalleableLogger, name.lower(), _make_method(val))


_install_level_methods()



LOGGER = MalleableLogger("traefik-hosts-to-unifi")

CONTAINER_HOSTNAME = socket.gethostname()

def sanitize_filename(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", text.strip())
    return cleaned or "unknown"

def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.ERROR)
    LOGGER.setLevel(level)
    LOGGER.handlers.clear()
    LOGGER.propagate = False

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    LOGGER.addHandler(handler)


_TLDEXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())

UNIFI_API_KEY = None

UNIFI_HOST = None
TRAEFIK_IP = None
TRAEFIK_DNS = None

# Traefik API endpoint via the traefik-api-proxy container
TRAEFIK_HOST = None
TRAEFIK_PORT = 8080
TRAEFIK_PATH = "/api/http/routers"
UNIFI_KEEP_FILE = None

DATA_DIR = "/data"

def getUrlForUnifiDNS (record_id: str|None = None) -> str:
    urlForDNS = "/proxy/network/v2/api/site/default/static-dns"
    if record_id:
        urlForDNS += f"/{record_id}"
    return urlForDNS

def apply_runtime_args(args: argparse.Namespace) -> None:
    global UNIFI_API_KEY
    global UNIFI_HOST
    global TRAEFIK_IP
    global TRAEFIK_DNS
    global TRAEFIK_HOST
    global TRAEFIK_PORT
    global TRAEFIK_PATH
    global UNIFI_KEEP_FILE


    UNIFI_API_KEY = args.unifi_api_key or os.getenv("UNIFI_API_KEY")
    UNIFI_HOST = args.unifi_host or os.getenv("UNIFI_HOST")
    TRAEFIK_IP = args.traefik_ip or os.getenv("TRAEFIK_IP")
    TRAEFIK_DNS = args.traefik_dns or os.getenv("TRAEFIK_DNS")
    TRAEFIK_HOST = args.traefik_host or os.getenv("TRAEFIK_HOST") or TRAEFIK_IP
    TRAEFIK_PORT = args.traefik_port or os.getenv("TRAEFIK_PORT") or TRAEFIK_PORT
    TRAEFIK_PATH = args.traefik_path or os.getenv("TRAEFIK_PATH") or TRAEFIK_PATH
    UNIFI_KEEP_FILE = args.unifi_keep_file or os.getenv("UNIFI_KEEP_FILE")

    LOGGER.debug(f"Applied runtime arguments:")
    LOGGER.debug(f"UNIFI_API_KEY  : {'*' * len(UNIFI_API_KEY) if UNIFI_API_KEY else None}")
    LOGGER.debug(f"UNIFI_HOST     : {UNIFI_HOST}")
    LOGGER.debug(f"TRAEFIK_IP     : {TRAEFIK_IP}")
    LOGGER.debug(f"TRAEFIK_DNS    : {TRAEFIK_DNS}")
    LOGGER.debug(f"TRAEFIK_HOST   : {TRAEFIK_HOST}")
    LOGGER.debug(f"TRAEFIK_PORT   : {TRAEFIK_PORT}")
    LOGGER.debug(f"TRAEFIK_PATH   : {TRAEFIK_PATH}")
    LOGGER.debug(f"UNIFI_KEEP_FILE: {UNIFI_KEEP_FILE}")


def get_traefik_routers() -> List[Dict]:
    """Fetch all HTTP routers from Traefik API"""
    try:
        # Use raw socket with HTTP/1.0
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((TRAEFIK_HOST, TRAEFIK_PORT))

        # Send HTTP/1.0 request
        request = f'GET {TRAEFIK_PATH} HTTP/1.0\r\nHost: {TRAEFIK_HOST}\r\nConnection: close\r\n\r\n'
        
        if LOGGER.getEffectiveLevel() <= logging.DEBUG:
            LOGGER.debug(f"{'GET'.ljust(6)} : Traefik : {TRAEFIK_HOST},{TRAEFIK_PORT}")
            for l in request.splitlines():
                LOGGER.debug(''.join(["  > ", l.replace('\r','')]))

        sock.sendall(request.encode())

        # Read response
        response_data = b''
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            response_data += chunk
        sock.close()

        # Parse HTTP response
        response_text = response_data.decode('utf-8')
        # Split headers and body
        header_end = response_text.find('\r\n\r\n')
        if header_end == -1:
            LOGGER.error("Invalid HTTP response from Traefik API")
            raise Exception("Invalid HTTP response from Traefik API")

        body = response_text[header_end + 4:]

        asList = json.loads(body)

        return asList

    except Exception as e:
        msg = f"Error connecting to Traefik API at {TRAEFIK_HOST}:{TRAEFIK_PORT}{TRAEFIK_PATH}: {e}"
        LOGGER.error(msg)
        raise Exception(msg)


def extract_hosts(rule: str) -> List[str]:
    """Extract all hostnames from a Traefik rule containing Host()"""
    # Match Host(`hostname`) or Host("hostname")
    pattern = r'Host\([`"]([^`"]+)[`"]\)'
    return re.findall(pattern, rule)


def determine_tld_from_fqdn(hostname: str) -> str:
    """Return the registrable domain portion of a hostname.

    Examples:
      - admin.caddy.darter.au -> darter.au
      - foo.bar.example.co.uk -> example.co.uk
    """
    host = (hostname or "").strip().strip(".").lower()
    if not host:
        return "*Unknown*"

    # Prefer Public Suffix List logic when tldextract is available.
    extracted = _TLDEXTRACTOR(host)
    registrable = getattr(extracted, "top_domain_under_public_suffix", "")
    if registrable:
        return registrable

    labels = [part for part in host.split(".") if part]
    if len(labels) < 2:
        return host

    # Handle common ccTLD-style registrable domains like example.co.uk.
    # If second-level label is co/com/ac/org and TLD looks like country code,
    # treat last 3 labels as registrable domain.
    common_second_level = {"ac", "co", "com", "edu", "gov", "id", "mil", "net", "nic", "org"}
    if (
        len(labels) >= 3
        and labels[-2] in common_second_level
        and len(labels[-1]) == 2
        and labels[-1].isalpha()
    ):
        return ".".join(labels[-3:])

    return ".".join(labels[-2:])


def decode_chunked(body: str) -> str:
    """Decode HTTP chunked transfer encoding"""
    lines = body.split('\r\n')
    result = []
    i = 0

    while i < len(lines):
        # Read chunk size (hex)
        if not lines[i]:
            i += 1
            continue

        try:
            chunk_size = int(lines[i], 16)
        except (ValueError, IndexError):
            break

        if chunk_size == 0:
            break

        # Read chunk data
        i += 1
        if i < len(lines):
            result.append(lines[i])

        i += 1

    return ''.join(result)


def unifi_api_request(method: str, path: str, data: dict = None) -> dict:
    """Make a request to the Unifi API"""
    try:
        if not UNIFI_API_KEY:
            raise Exception("UNIFI_API_KEY is not set")
        # Create SSL context that doesn't verify certificates (self-signed)

        if LOGGER.getEffectiveLevel() <= logging.DEBUG:
            line = f"{method.ljust(6)} : UniFi   : {UNIFI_HOST}{path}"
            if data:
                line += " with data:"
                LOGGER.debug(line)
                LOGGER.debug(json.dumps(data, indent=2,default=str))
            else:
                LOGGER.debug(line)

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        # Create socket and wrap with SSL
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        ssl_sock = context.wrap_socket(sock, server_hostname=UNIFI_HOST)
        ssl_sock.connect((UNIFI_HOST, 443))

        # Build request
        headers = [
            f"{method} {path} HTTP/1.1",
            f"Host: {UNIFI_HOST}",
            f"X-API-KEY: {UNIFI_API_KEY}",
            "Content-Type: application/json",
            "Connection: close"
        ]

        body = ""
        if data:
            body = json.dumps(data)
            headers.append(f"Content-Length: {len(body)}")

        request = "\r\n".join(headers) + "\r\n\r\n" + body
        ssl_sock.sendall(request.encode())

        # Read response
        response_data = b''
        while True:
            chunk = ssl_sock.recv(8192)
            if not chunk:
                break
            response_data += chunk
        ssl_sock.close()

        # Parse response
        response_text = response_data.decode('utf-8')
        header_end = response_text.find('\r\n\r\n')
        if header_end == -1:
            return {"error": "Invalid HTTP response"}

        # Extract status code
        headers_text = response_text[:header_end]
        status_line = headers_text.split('\r\n')[0]
        status_code = int(status_line.split()[1]) if len(status_line.split()) > 1 else 0

        body_text = response_text[header_end + 4:].strip()

        # Check if response is chunked
        if 'transfer-encoding: chunked' in headers_text.lower():
            body_text = decode_chunked(body_text)

        # Empty body is OK for DELETE or 2xx responses
        if not body_text:
            if method == "DELETE" or (200 <= status_code < 300):
                return {"success": True, "status_code": status_code}
            return {"error": "Empty response body"}

        try:
            result = json.loads(body_text)
        except json.JSONDecodeError as e:
            return {"error": f"JSON decode error: {e}", "raw_body": body_text[:500]}

        # Check if response indicates error
        if isinstance(result, dict):
            meta = result.get("meta", {})
            if meta.get("rc") == "error":
                return {"error": f"API Error: {meta.get('msg', 'Unknown')}", "raw_response": result}

        return result
    except Exception as e:
        return {"error": str(e)}

def create_dns_record(hostname: str, record_type: str, value : str) -> bool|dict:
    """
    Create a new DNS record in Unifi

    Args:
        hostname: The hostname to create (e.g., 'whoami.darter.au')
        record_type: The type of DNS record (e.g., 'A', 'CNAME')
        value: The value for the DNS record (e.g., IP address for 'A' record)

    Returns:
        True if successful, False otherwise
    """
    LOGGER.debug(f"create_dns_record({hostname=}, {record_type=}, {value=})")
    create_data = {
        "key": hostname,
        "value": value,
        "record_type": record_type,
        "enabled": True
    }
    result = unifi_api_request(
        "POST",
        getUrlForUnifiDNS(),
        create_data
    )
    LOGGER.debug(result)
    if "errorCode" in result:
        msgText = f"Error creating '{record_type}' DNS record for {hostname}: {result['message']}"
        LOGGER.error(msgText)
        raise Exception(msgText)
    return result

def create_dns_CNAME_record(hostname: str, cname: str|None = None) -> bool|dict:
    if cname is None:
        cname = TRAEFIK_DNS
    return create_dns_record(hostname, "CNAME", cname)

def create_dns_A_record(hostname: str, ip: str|None = None) -> bool|dict:
    """
    Create a new DNS record in Unifi

    Args:
        hostname: The hostname to create (e.g., 'whoami.darter.au')
        ip: The IP address to point to (defaults to TRAEFIK_IP)

    Returns:
        True if successful, False otherwise
    """
    if ip is None:
        ip = TRAEFIK_IP
    return create_dns_record(hostname, "A", ip)


def update_dns_record(hostname: str, record_id: str, ip: str|None = None) -> bool:
    """
    Update an existing DNS record in Unifi

    Args:
        hostname: The hostname to update
        record_id: The _id of the existing record
        ip: The IP address to point to (defaults to TRAEFIK_IP)

    Returns:
        True if successful, False otherwise
    """
    if ip is None:
        ip = TRAEFIK_IP
    try:
        update_data = {
            "key": hostname,
            "value": ip,
            "record_type": "A"
        }
        result = unifi_api_request(
            "PUT",
            getUrlForUnifiDNS(record_id),
            update_data
        )
        if "errorCode" in result:
            msgText = f"Error updating DNS record for {hostname}: {result['message']}"
            LOGGER.error(msgText)
            return False
        LOGGER.info("Updated DNS record: %s -> %s", hostname, ip)
        return True
    except Exception as e:
        LOGGER.error("Exception updating DNS record for %s: %s", hostname, e)
        return False


def delete_dns_record(record_id: str) -> bool:
    """
    Delete a DNS record from Unifi by ID

    Args:
        record_id: The _id of the record to delete

    Returns:
        True if successful, False otherwise
    """
    result = unifi_api_request(
        "DELETE",
        getUrlForUnifiDNS(record_id)
    )
    if "errorCode" in result:
        msgText = f"Error deleting DNS record {record_id}: {result['message']}"
        LOGGER.error(msgText)
        raise Exception(msgText)
    # print(f"Deleted DNS record: {record_id}")
    return result.get("success", False)


def create_or_update_dns_record(hostname: str, ip: str|None = None) -> bool:
    """
    Create or update a DNS record in Unifi pointing hostname to the given IP

    Args:
        hostname: The hostname to create/update (e.g., 'whoami.darter.au')
        ip: The IP address to point to (defaults to TRAEFIK_IP)

    Returns:
        True if successful, False otherwise
    """
    if ip is None:
        ip = TRAEFIK_IP
    try:
        # Get existing DNS records (policy engine v2 API)
        result = unifi_api_request("GET", getUrlForUnifiDNS())

        if "error" in result:
            LOGGER.error("Error fetching DNS records: %s", result["error"])
            return False

        # Result is a list of records
        records = result if isinstance(result, list) else result.get("data", [])

        # Check if record exists
        existing_record = None
        for record in records:
            if record.get("key") == hostname:
                existing_record = record
                break

        if existing_record:
            # Update existing record
            return update_dns_record(hostname, existing_record.get("_id"), ip)
        else:
            # Create new record
            return create_dns_A_record(hostname, ip)

    except Exception as e:
        LOGGER.error("Exception in create_or_update_dns_record for %s: %s", hostname, e)
        return False


def get_unifi_dns_records() -> List[Dict]:
    """
    Fetch all DNS records from Unifi Dream Machine (new policy engine)

    Returns:
        List of DNS records
    """
    result = unifi_api_request("GET", getUrlForUnifiDNS())

    if "error" in result:
        LOGGER.error("Error fetching DNS records: %s", result["error"])
        raise Exception(f"API Error: {result['error']}")

    # Result should be a list of DNS records
    if isinstance(result, list):
        ...
    elif "data" not in result:
        LOGGER.error("Unexpected API response format: %s", result)
        raise Exception(f"Unexpected API response format: {result}")
    else:
        result = result.get("data", [])

    for ix, r in enumerate(result):
        thjs = {"tld": determine_tld_from_fqdn(r.get("key", "")), **r}
        result[ix] = thjs

    def recSort (record):
        order= ["tld", "key", "record_type", "value"]
        retparts = [record.get(k,'~') for k in order]
        for ix, v in enumerate(retparts):
            if isinstance(v, str):
                v = v.lower()
                if '.' in v:
                    splitV = v.split(".")[::-1]
                    v = '.'.join(splitV)
                retparts[ix] = v
        return retparts
    
    result.sort(key=recSort)

    dumpAsJSON(
        theData = result,
        nameParts = [CONTAINER_HOSTNAME, "unifiDNS", UNIFI_HOST],
        msg = "Writing UniFi DNS records"
    )

    return result

def display_unifi_dns_records(displayID: bool = False):
    """Fetch and display all DNS records from UDM"""
    lines = get_unifi_dns_records_md(displayID)
    for line in lines:
        print(line)

def get_unifi_dns_records_md(displayID: bool = False) -> list[str]:
    """Fetch and display all DNS records from UDM"""
    records = get_unifi_dns_records()

    theDisplayLines = []
    theDisplayLines.append("")
    theDisplayLines.append(f"## UDM DNS Records ({len(records)})")
    theDisplayLines.append("")

    if not records:
        line = "No DNS records found or unable to connect to UDM API"
        theDisplayLines.append(line)
        LOGGER.warning(line)
        return theDisplayLines

    colHeads = {
        "tld": "TLD",
        "key": "Hostname",
        "record_type": "Record Type",
        "value": "Value",
        "enabled": "Enabled",
        '_id' : "ID",
    }
    if not displayID:
        _ = colHeads.pop('_id','')

    colWidths = {k: len(v) for k,v in colHeads.items()}

    for ix,record in enumerate(records):
        for k,v in colWidths.items():
            thisLen = len(str(record.get(k, '')))
            if thisLen > v:
                colWidths[k] = thisLen + 2
        records[ix] = record

    line = "| "
    for k, width in colWidths.items():
        line += f"{colHeads[k].rjust(width)} | "
    theDisplayLines.append(line.strip())

    line = "| "
    for v in colWidths.values():
        line += f"{'-' * v} | "
    theDisplayLines.append(line.strip())


    lastTLD = colHeads.get('tld')
    for record in records:

        line = "| "
        for k, width in colWidths.items():
            value = record.get(k, '')
            if k == 'tld':
                if value == lastTLD:
                    value = " "
                else:
                    lastTLD = value

            line += f"{str(value).rjust(width)} | "
        theDisplayLines.append(line.strip())

    return theDisplayLines

def display_traefik_host_entries():
    lines = get_traefik_host_entries_md()
    for line in lines:
        print(line)

def dumpAsJSON (theData, nameParts: list[str],msg: str = "Dumping data to JSON file"):
    if LOGGER.getEffectiveLevel() <= logging.DEBUG:
        if not nameParts[-1].lower() == "json":
            nameParts.append("json")
        fname = sanitize_filename('.'.join(nameParts))
        jsonFile = Path(DATA_DIR).joinpath(fname)
        LOGGER.debug(f"{msg} to '{fname}'")
        jsonData = json.dumps(theData, indent=2, default=str)
        atomic_write(jsonFile, jsonData)

def get_exploded_traefik_routers() -> List[Dict]:
    exploded = []
    for router in get_traefik_routers():
        rule = router.get("rule", "")
        if "Host(" not in rule:
            continue
        hosts = extract_hosts(rule)
        router_name = router.get("name", "unknown")
        if "@" in router_name:
            router_name, router_source = router_name.split("@")[:2]
        else:
            router_source = "unknown"
        for host in hosts:
            row = {
                "tld": determine_tld_from_fqdn(host),
                "hostname": host,
                "name": router_name,
                "source": router_source,
                "entryPoints": ", ".join(router.get("entryPoints", [])),
                "status": router.get("status", "unknown"),
            }
            row.update({k:v for k,v in router.items() if k not in row})
            exploded.append(row)

    def recSort(record):
        retparts = [record.get(k, "") for k in list(record.keys())[:6]]
        for ix, val in enumerate(retparts):
            if isinstance(val, str):
                low = val.lower()
                if "." in low:
                    low = ".".join(low.split(".")[::-1])
                retparts[ix] = low
        return retparts

    exploded.sort(key=recSort)

    dumpAsJSON(
        theData = exploded,
        nameParts = [CONTAINER_HOSTNAME, "traefikRouters", TRAEFIK_IP],
        msg = "Writing traefik routers"
    )

    return exploded


def get_traefik_host_entries_md() -> list[str]:
    routerDisp = get_exploded_traefik_routers()
    theDisplayLines = ["", f"## Traefik Host Entries ({len(routerDisp)})", ""]
    if not routerDisp:
        line = "No routers found or unable to connect to Traefik API"
        LOGGER.warning(line)
        theDisplayLines.append(line)
        return theDisplayLines


    colHeads = {
        "tld": "TLD",
        "hostname": "Hostname",
        "name": "Router Name",
        "source": "Source",
        "entryPoints": "EntryPoints",
        "status": "Status",
    }
    colWidths = {k: len(v) for k, v in colHeads.items()}

    for row in routerDisp:
        for k, v in [(k,v) for k,v in row.items() if k in colWidths]:
            colWidths[k] = max(colWidths[k], len(str(v)) + 2)



    line = "| "
    for k, width in colWidths.items():
        line += f"{colHeads[k].rjust(width)} | "
    theDisplayLines.append(line.strip())

    line = "| "
    for v in colWidths.values():
        line += f"{'-' * v} | "
    theDisplayLines.append(line.strip())

    lastTLD = colHeads.get("tld")
    for record in routerDisp:
        line = "| "
        for k, width in colWidths.items():
            value = record.get(k, "")
            if k == "tld":
                if value == lastTLD:
                    value = " "
                else:
                    lastTLD = value
            line += f"{str(value).rjust(width)} | "
        theDisplayLines.append(line.strip())

    return theDisplayLines



def update_unifi_from_traefik():
    """Fetch Traefik routers and ensure corresponding DNS records exist in UDM"""
    routers = get_exploded_traefik_routers()

    if not routers:
        LOGGER.warning("No routers found or unable to connect to Traefik API")
        return

    unifi_dns = {u.get("key"): u for u in get_unifi_dns_records()}

    allHosts = set()

    LOGGER.info(f"Processing {len(routers)} Traefik routers to sync with UniFi DNS")

    for router in routers:
        host = router.get('hostname','')
        allHosts.add(host)
        if host == TRAEFIK_DNS:
            continue
        record = unifi_dns.get(host,{})
        if record:
            if record.get('record_type') != 'CNAME':
                continue
            if not (record.get('value') == TRAEFIK_DNS and record.get('enabled', False) == True):
                LOGGER.debug(f"{host} : Existing record does not match expected CNAME to Traefik. Deleting")
                delete_dns_record(record.get('_id'))
                record = None

        if not record:
            LOGGER.info(f"{host} : creating in Unifi DNS records")
            unifi_dns[host] = create_dns_CNAME_record(host)



    extraOnes = []


    preserveList = []
    if UNIFI_KEEP_FILE:
        try:
            preserveListFile = Path(UNIFI_KEEP_FILE)
        except Exception as e:
            preserveListFile = None
        try:
            if preserveListFile and preserveListFile.is_file():
                preserveListText = preserveListFile.read_text()
                preserveList = json.loads(preserveListText)
        except Exception as e:
            LOGGER.error("Error reading preserve list file %s: %s", UNIFI_KEEP_FILE, e)


    for dns in unifi_dns.values():
        thisKey = dns.get('key','')
        thisRecordType = dns.get('record_type','')
        thisValue = dns.get('value','')
        if thisKey == TRAEFIK_DNS:
            continue
        if (
            thisKey not in allHosts
            and thisKey not in preserveList
            and
            (
                (thisRecordType == 'A' and thisValue == TRAEFIK_IP)
             or (thisRecordType == 'CNAME' and thisValue == TRAEFIK_DNS)
            )
        ):
            extraOnes.append(dns)

    if extraOnes:
        LOGGER.info(
            "Removing extra DNS records from UDM which point to Traefik that are not in Traefik"
        )
        for extra in extraOnes:
            LOGGER.info(
                " - %s exists in UDM but not in Traefik, deleting from UDM",
                extra.get("key"),
            )
            delete_dns_record(extra.get('_id'))
    # print (extraOnes)

def remove_all_traefik_dns_records_from_unifi():
    """Remove all DNS records from UDM that point to Traefik"""

    unifi_dns = {u.get("key"): u for u in get_unifi_dns_records()}

    for dns in unifi_dns.values():
        # print (dns)

        thisKey = dns.get('key','')
        thisRecordType = dns.get('record_type','')
        thisValue = dns.get('value','')
        # print (f"{thisRecordType=}, {thisKey=}, {thisValue=}")
        if (
            (thisRecordType == 'A' and thisValue == TRAEFIK_IP and thisKey != TRAEFIK_DNS)
            or (thisRecordType == 'CNAME' and thisValue == TRAEFIK_DNS)
        ):
            # print (f" - {thisKey} is a CNAME record pointing to {thisValue}, deleting from UDM as it should not be there")
            delete_dns_record(dns.get('_id'))


def atomic_write(path: Path, content: str|list[str]) -> None:
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if isinstance(content, list):
        content = "\n".join(content)
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)

def get_all_the_lines(doUnifi: bool| None = None) -> list[str]:
    lines = get_traefik_host_entries_md()
    if doUnifi is None:
        doUnifi = DO_UNIFI_API_CALLS
    if doUnifi:
        lines.extend(get_unifi_dns_records_md())
    return lines

def displayTheEntries(doUnifi: bool| None = None):
    if doUnifi is None:
        doUnifi = DO_UNIFI_API_CALLS
    lines = get_all_the_lines(doUnifi)
    for line in lines:
        print(line)


def extractTheEntries(doUnifi: bool| None = None):
    if doUnifi is None:
        doUnifi = DO_UNIFI_API_CALLS
    lines = get_all_the_lines(doUnifi)
    extractPath = Path(DATA_DIR).joinpath("extracted_hosts.md")
    atomic_write(extractPath, lines)
    # lvl = LOGGER.getEffectiveLevel()
    LOGGER.info(f"Extracted host entries written to {extractPath}", force = True)


def run_sync():

    # display_traefik_host_entries()
    update_unifi_from_traefik()
    if LOGGER.getEffectiveLevel() <= logging.DEBUG:
        LOGGER.info("Sync complete, displaying current Traefik host entries and UDM DNS records:")
        displayTheEntries()
    # display_unifi_dns_records()

action_map = {
    "display": displayTheEntries,
    "markdown": extractTheEntries,
    "sync": run_sync,
    "remove-traefik-dns": remove_all_traefik_dns_records_from_unifi
}

action_needs_unifi = [k for k in action_map.keys() if k in ("sync", "remove-traefik-dns")]

def cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Traefik hosts to UniFi DNS")
    action_map_keys = list(action_map.keys())
    action_default = action_map_keys[0]

    env_unifi_api_key = os.getenv("UNIFI_API_KEY")
    env_unifi_host = os.getenv("UNIFI_HOST")
    env_traefik_ip = os.getenv("TRAEFIK_IP")
    env_traefik_dns = os.getenv("TRAEFIK_DNS")
    env_traefik_host = os.getenv("TRAEFIK_HOST")
    env_traefik_port = int(os.getenv("TRAEFIK_PORT", "8080"))
    env_traefik_path = os.getenv("TRAEFIK_PATH", "/api/http/routers")
    env_unifi_keep_file = os.getenv("UNIFI_KEEP_FILE")
    env_log_level = os.getenv("LOG_LEVEL", "ERROR").upper()

    parser.add_argument(
        "--action",
        choices=action_map_keys,
        default=action_default,
        type=lambda s: s.lower(),
        help=f"Action to perform (default: {action_default})",
    )

    parser.add_argument(
        "--unifi-api-key",
        default=env_unifi_api_key,
        required=False,
        help="UniFi API key (default: UNIFI_API_KEY)",
    )
    parser.add_argument(
        "--unifi-host",
        default=env_unifi_host,
        required=False,
        help=f"UniFi host (default: {env_unifi_host})",
    )
    parser.add_argument(
        "--traefik-ip",
        default=env_traefik_ip,
        required=env_traefik_ip is None,
        help=f"Traefik IP address (default: {env_traefik_ip})",
    )
    parser.add_argument(
        "--traefik-dns",
        default=env_traefik_dns,
        required=env_traefik_dns is None,
        help=f"Traefik DNS hostname (default: {env_traefik_dns})",
    )
    parser.add_argument(
        "--traefik-host",
        default=env_traefik_host,
        help=f"Traefik API host (default: {env_traefik_host} or --traefik-ip)",
    )
    parser.add_argument(
        "--traefik-port",
        type=int,
        default=env_traefik_port,
        help=f"Traefik API port (default: {env_traefik_port} or 8080)",
    )
    parser.add_argument(
        "--traefik-path",
        default=env_traefik_path,
        help=f"Traefik API path (default: {env_traefik_path} or /api/http/routers)",
    )
    parser.add_argument(
        "--unifi-keep-file",
        default=env_unifi_keep_file,
        help="JSON file with preserved hostnames"
        )

    parser.add_argument(
        "--log-level",
        default=env_log_level,
        choices=[v[0] for v in sorted([(k,v) for v,k in logging._levelToName.items() if v != logging.NOTSET],key = lambda kv: kv[1])],
        type=lambda s: s.upper(),
        help="Log verbosity level (default: ERROR).",
    )

    args = parser.parse_args()
    configure_logging(args.log_level)
    LOGGER.debug(args)
    global DO_UNIFI_API_CALLS
    DO_UNIFI_API_CALLS = bool(args.unifi_api_key and args.unifi_host)
    if not DO_UNIFI_API_CALLS and args.action in action_needs_unifi:
        parser.error(f"--action {args.action} requires --unifi-api-key and --unifi-host to be set")

    return args


def main() -> int:
    args = cli_args()
    configure_logging(args.log_level)

    apply_runtime_args(args)


    action = action_map.get(args.action)
    if action:
        action()
        return 0
    return 1

if __name__ == "__main__":
    raise SystemExit(main())

