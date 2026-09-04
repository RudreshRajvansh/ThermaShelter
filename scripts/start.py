from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
WEB = ROOT / "web"
DATA = ROOT / "data"
BUILD = ROOT / "build"

API_PORT = 8000
WEB_PORT = 5173

RULE = "-" * 78


def say(text: str = "") -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(text.encode(encoding, "replace").decode(encoding), flush=True)


def step(number: int, total: int, title: str) -> None:
    say()
    say(f"[{number}/{total}] {title}")
    say(RULE)


def fail(message: str, remedy: str = "") -> None:
    say()
    say(f"stopped: {message}")
    if remedy:
        say(f"  {remedy}")
    raise SystemExit(1)


def venv_python() -> Path:
    for candidate in (ENGINE / ".venv" / "Scripts" / "python.exe",
                      ENGINE / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


def run(command: list[str], cwd: Path | None = None,
        env: dict[str, str] | None = None, quiet: bool = False) -> int:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    result = subprocess.run(
        command, cwd=str(cwd or ROOT), env=merged,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.STDOUT if quiet else None, text=True)
    if quiet and result.returncode != 0 and result.stdout:
        say(result.stdout.strip()[-3000:])
    return result.returncode


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def wait_for(url: str, timeout_s: float, process: subprocess.Popen) -> dict | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return None
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            time.sleep(0.5)
    return None


def energyplus_candidates() -> list[Path]:
    found: list[Path] = []
    given = os.environ.get("ENERGYPLUS_DIR")
    if given:
        found.append(Path(given))
    found.append(ROOT / "tools" / "EnergyPlus")
    found.extend(sorted((ROOT / "tools").glob("EnergyPlus*")))
    binary = shutil.which("energyplus")
    if binary:
        found.append(Path(binary).parent)
    if os.name == "nt":
        for drive in ("C:/", "D:/"):
            found.extend(sorted(Path(drive).glob("EnergyPlusV*")))
    else:
        found.extend(sorted(Path("/usr/local").glob("EnergyPlus*")))
        found.append(Path("/opt/EnergyPlus"))
    seen: set[str] = set()
    unique: list[Path] = []
    for path in found:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def find_energyplus(python: Path) -> dict | None:
    probe = ("import json,sys;"
             "sys.path.insert(0,r'%s');"
             "from shelter_engine.config import find_energyplus;"
             "i=find_energyplus();"
             "print(json.dumps({'dir':str(i.root),'version':i.version_string}))"
             % (ENGINE / "src"))
    for candidate in energyplus_candidates():
        if not (candidate / "Energy+.idd").is_file():
            continue
        result = subprocess.run([str(python), "-c", probe], capture_output=True,
                                text=True,
                                env={**os.environ, "ENERGYPLUS_DIR": str(candidate)})
        if result.returncode != 0:
            continue
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            continue
    result = subprocess.run([str(python), "-c", probe], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return None


def extract_layer0(archive: Path) -> Path:
    target = BUILD / "layer0"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    for candidate in (target, *sorted(target.iterdir())):
        if candidate.is_dir() and (candidate / "weather").is_dir():
            return candidate
    return target


def locate_layer0(given: str | None) -> Path | None:
    if given:
        path = Path(given)
        if path.is_file() and path.suffix.lower() == ".zip":
            return extract_layer0(path)
        if path.is_dir():
            return path
        fail(f"{path} is neither a directory nor a zip archive")
    for candidate in sorted(BUILD.glob("layer0/*")):
        if candidate.is_dir() and (candidate / "weather").is_dir():
            return candidate
    for archive in sorted(ROOT.glob("layer_0*.zip")) + sorted(
            Path.home().glob("Downloads/layer_0*.zip")):
        return extract_layer0(archive)
    return None


def data_is_ingested() -> bool:
    return ((DATA / "materials.db").is_file()
            and any((DATA / "sites").glob("*.json"))
            and any((DATA / "weather").glob("*.epw")))


def describe_cycle(base: str) -> None:
    say()
    say("THE CYCLE, END TO END")
    say(RULE)
    say("  input   the officer answers six questions on Site Input: where the shelter")
    say("          stands, what it is for, how many people, which way it faces, when")
    say("          it must perform, and what may be built with. Everything else is")
    say("          read from Layer 0 or computed.")
    say()
    say("  build   the answers become one DesignSpec. From that single description the")
    say("          engine derives the room layout, the wall build ups, the windows on")
    say("          each facade and the shading the ridge line casts. The drawing on")
    say("          screen and the model the solver reads come from the same object, so")
    say("          they cannot disagree.")
    say()
    say("  solve   EnergyPlus runs the period hour by hour against the real weather")
    say("          year. Temperatures, humidity, per surface heat flows and any solver")
    say("          warnings come back from eplusout.sql and eplusout.err.")
    say()
    say("  judge   comfort against the adaptive model, condensation risk, cost,")
    say("          buildability, and the same run repeated for a current practice")
    say("          building of the same size so the difference is like for like.")
    say("          Eight more runs at the corners of the uncertain inputs give every")
    say("          headline number a band instead of a false decimal.")
    say()
    say("  output  the result is stored under its design hash and served as JSON. The")
    say("          interface reads it and shows the 3D sun study, the plan and")
    say("          sections, the hourly curves, the comparison table and the report.")
    say("          Nothing on those screens is computed in the browser.")
    say()
    say("  search  ask for the trade off front and a genetic algorithm evolves a")
    say("          population of designs, every one of them a real EnergyPlus run, and")
    say("          returns the set where you cannot get warmer without paying more.")
    say()
    say("WHERE TO CLICK")
    say(RULE)
    say(f"  interface        {base}")
    say(f"  API reference    http://127.0.0.1:{API_PORT}/docs")
    say(f"  what is missing  http://127.0.0.1:{API_PORT}/api/meta")
    say()
    say("  Stop everything with Ctrl+C.")


def main() -> int:
    global API_PORT, WEB_PORT
    parser = argparse.ArgumentParser(
        description="Bring the whole tool up from a cold checkout: verify the "
                    "install, turn the Layer 0 delivery into the data directory, "
                    "precompute a few results so the first screen is not empty, and "
                    "run the API and the interface.")
    parser.add_argument("--layer0", default=None,
                        help="the Layer 0 zip or extracted directory")
    parser.add_argument("--reingest", action="store_true",
                        help="rebuild the data directory even if it already exists")
    parser.add_argument("--recompute", action="store_true",
                        help="run the precomputed simulations again")
    parser.add_argument("--no-precompute", action="store_true")
    parser.add_argument("--quick", action="store_true",
                        help="fewer precomputed designs and no uncertainty sweep")
    parser.add_argument("--check", action="store_true",
                        help="verify the install and the data, then stop")
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--api-port", type=int, default=API_PORT)
    parser.add_argument("--web-port", type=int, default=WEB_PORT)
    parser.add_argument("--serve-built", action="store_true",
                        help="serve the production build instead of the dev server")
    args = parser.parse_args()

    API_PORT, WEB_PORT = args.api_port, args.web_port

    total = 4 if args.check else 6
    say()
    say("PASSIVE SOLAR SHELTER DESIGN TOOL")
    say(RULE)
    say(f"  repository  {ROOT}")

    step(1, total, "Checking the install")
    python = venv_python()
    say(f"  python           {python}")
    if run([str(python), "-c", "import shelter_engine, shelter_api"],
           quiet=True) != 0:
        fail("the engine and API packages are not importable",
             "cd engine && python -m venv .venv && "
             ".venv/Scripts/python -m pip install -e \".[dev]\" && "
             ".venv/Scripts/python -m pip install -e ../api --no-deps")
    say("  packages         shelter_engine and shelter_api import cleanly")

    install = find_energyplus(python)
    if install is None:
        fail("EnergyPlus was not found",
             "Install EnergyPlus 24.2.0 and point ENERGYPLUS_DIR at the directory "
             "holding energyplus and Energy+.idd. Nothing is simulated without it, "
             "and no result is invented in its place.")
    say(f"  EnergyPlus       {install['version']} at {install['dir']}")

    node = shutil.which("npm")
    if node is None and not args.api_only:
        say("  npm              not found, so only the API will start")
        args.api_only = True
    elif not args.api_only:
        say(f"  npm              {node}")

    step(2, total, "Layer 0 data")
    if args.reingest or not data_is_ingested():
        source = locate_layer0(args.layer0)
        if source is None:
            fail("no Layer 0 delivery found",
                 "Pass it with --layer0 path/to/layer_0.zip, or extract it under "
                 f"{BUILD / 'layer0'}.")
        say(f"  reading          {source}")
        if run([str(python), str(ROOT / "scripts" / "ingest_layer0.py"),
                "--source", str(source), "--out", str(DATA)],
               env={"ENERGYPLUS_DIR": str(install["dir"])}) != 0:
            fail("the Layer 0 ingestion failed")
    else:
        say(f"  already built    {DATA}")
        manifest = DATA / "layer0_manifest.json"
        if manifest.is_file():
            record = json.loads(manifest.read_text(encoding="utf-8"))
            say(f"  weather          {record.get('weather_file')}")
            say(f"  from Layer 0     "
                f"{len(record.get('materials_from_layer0', []))} materials, "
                f"{len(record.get('pcm_from_layer0', []))} phase change materials")
            added = record.get("materials_added", []) + record.get("glazing_added", [])
            if added:
                say(f"  filled in here   {len(added)} records carry an ASHRAE source "
                    "marked pending, not a Layer 0 one")
            for name, reason in (record.get("corrections") or {}).items():
                say(f"  corrected        {name}: {reason[:150]}")
        say("  pass --reingest to rebuild it from the delivery")

    step(3, total, "Precomputed results")
    runs = DATA / "runs.db"
    if args.no_precompute:
        say("  skipped on request")
    elif args.recompute or not runs.is_file():
        command = [str(python), str(ROOT / "scripts" / "precompute.py"),
                   "--data", str(DATA)]
        if args.quick:
            command.append("--quick")
        if run(command, env={"ENERGYPLUS_DIR": str(install["dir"]),
                             "SHELTER_DATA_DIR": str(DATA)}) != 0:
            fail("the precomputation failed")
    else:
        say(f"  already stored   {runs}")
        say("  pass --recompute to run them again")

    if args.check:
        step(4, total, "Ready")
        say("  The install, the data and the stored results are all in place.")
        say("  Run this script without --check to bring the tool up.")
        return 0

    environment = {"SHELTER_DATA_DIR": str(DATA),
                   "ENERGYPLUS_DIR": str(install["dir"]),
                   "PYTHONUNBUFFERED": "1"}

    step(4, total, "Starting the API")
    if port_open(API_PORT):
        holder = ""
        if shutil.which("docker"):
            listing = subprocess.run(
                ["docker", "ps", "--filter", f"publish={API_PORT}",
                 "--format", "{{.Names}}"], capture_output=True, text=True)
            names = [line for line in listing.stdout.split() if line]
            if names:
                holder = (f"The container {', '.join(names)} is publishing it. "
                          "Either use that stack, which is already serving the same "
                          "API, or run `docker compose down` first.")
        fail(f"port {API_PORT} is already in use",
             holder or ("Stop whatever is on it, shut down a previous run of this "
                        "script, or pass --api-port to use a different one."))
    api = subprocess.Popen(
        [str(python), "-m", "uvicorn", "shelter_api.main:app",
         "--host", "127.0.0.1", "--port", str(API_PORT)],
        cwd=str(ROOT), env={**os.environ, **environment})
    processes = [api]

    try:
        health = wait_for(f"http://127.0.0.1:{API_PORT}/api/health", 60.0, api)
        if health is None:
            fail("the API did not come up")
        say(f"  status           {health.get('status')}  "
            f"{health.get('detail', '')}")
        for warning in health.get("warnings", []):
            say(f"  ! [{warning.get('code')}] {str(warning.get('message'))[:110]}")
        if not health.get("ready"):
            say("  the API started but reports it is not ready. Simulation requests "
                "will return an explanation rather than a number.")

        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{API_PORT}/api/meta", timeout=5) as response:
                meta = json.loads(response.read().decode("utf-8"))
            gaps = meta.get("provenance_gaps") or []
            say(f"  sites            "
                f"{', '.join(site['id'] for site in meta.get('sites', []))}")
            say(f"  EnergyPlus       "
                f"{'available' if meta.get('energyplus_available') else 'MISSING, so '
                   'every simulation request will return an explanation rather than '
                   'a number'}")
            if gaps:
                say(f"  provenance gaps  {len(gaps)}, listed on every screen:")
                for gap in gaps[:6]:
                    text = gap if isinstance(gap, str) else gap.get("message", str(gap))
                    say(f"                   - {text[:110]}")
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            pass

        step(5, total, "Starting the interface")
        web_environment = {"VITE_API_URL": f"http://127.0.0.1:{API_PORT}"}
        base = f"http://127.0.0.1:{API_PORT}/docs"
        if args.api_only:
            say("  skipped, API only")
        else:
            if not (WEB / "node_modules").is_dir():
                say("  installing front end dependencies, this happens once")
                if run([node, "install"], cwd=WEB, quiet=True) != 0:
                    fail("npm install failed")

            schema = WEB / "src" / "lib" / "schema.d.ts"
            spec = BUILD / "openapi.json"
            spec.parent.mkdir(parents=True, exist_ok=True)
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{API_PORT}/openapi.json",
                        timeout=10) as response:
                    spec.write_bytes(response.read())
                npx = shutil.which("npx")
                if npx and run([npx, "openapi-typescript", str(spec),
                                "-o", str(schema)], cwd=WEB, quiet=True) == 0:
                    say("  types            regenerated from the running API, so the "
                        "interface cannot drift from the engine schema")
            except (urllib.error.URLError, OSError, TimeoutError):
                say("  types            left as they are, the API spec could not be "
                    "read")

            if args.serve_built:
                if run([node, "run", "build"], cwd=WEB) != 0:
                    fail("the production build failed")
                web = subprocess.Popen(
                    [node, "run", "preview", "--", "--host", "127.0.0.1",
                     "--port", str(WEB_PORT)],
                    cwd=str(WEB), env={**os.environ, **web_environment})
            else:
                web = subprocess.Popen(
                    [node, "run", "dev", "--", "--host", "127.0.0.1",
                     "--port", str(WEB_PORT)],
                    cwd=str(WEB), env={**os.environ, **web_environment})
            processes.append(web)
            for _ in range(60):
                if port_open(WEB_PORT) or web.poll() is not None:
                    break
                time.sleep(0.5)
            if web.poll() is not None:
                fail("the interface did not start")
            base = f"http://127.0.0.1:{WEB_PORT}"
            say(f"  serving          {base}")

        step(6, total, "Running")
        describe_cycle(base)

        while True:
            for process in processes:
                if process.poll() is not None:
                    say()
                    say(f"a process exited with code {process.returncode}, "
                        "shutting the rest down")
                    return process.returncode or 1
            time.sleep(1.0)
    except KeyboardInterrupt:
        say()
        say("stopping")
        return 0
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                try:
                    if os.name == "nt":
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                except (OSError, ValueError, AttributeError):
                    pass
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
