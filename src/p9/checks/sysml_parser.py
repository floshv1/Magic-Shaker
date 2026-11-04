"""Validation des .sysml générés avec l'OMG SysML v2 Pilot Implementation.

`python -m p9.checks.sysml_parser setup`            installe le parseur (kernel + JDK 21 si besoin)
`python -m p9.checks.sysml_parser validate f1 f2…`  valide les fichiers (ordre des imports respecté ;
                                                     `--` sépare des sessions indépendantes)
`python -m p9.checks.sysml_parser validate-out`     valide out/sysml/*.sysml puis out/model.sysml

Contraintes connues de l'outil (contournées ici) :
  - le kernel 0.63 est compilé pour Java 21 ;
  - sa bibliothèque standard est rangée dans des dossiers à espaces que `loadLibrary` décode mal
    sous Windows : on en fait une copie aux noms sans espaces et on la charge via tools/sysml/P9Validate.java ;
  - le dossier de travail (`sysml_home`) ne doit pas contenir d'espace.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from pydantic import BaseModel, Field

from p9.config import OmgSettings, load_settings

JAVA_HELPER = "P9Validate"


class SysmlIssue(BaseModel):
    file: str
    severity: str
    line: int
    column: int
    message: str


class FileResult(BaseModel):
    file: str
    errors: int
    warnings: int


class ValidationResult(BaseModel):
    files: list[FileResult] = Field(default_factory=list)
    issues: list[SysmlIssue] = Field(default_factory=list)
    stderr: str = ""
    returncode: int = 0

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and all(f.errors == 0 for f in self.files) and bool(self.files)


class ParserUnavailable(RuntimeError):
    pass


# --------------------------------------------------------------------- installation

def _home(cfg: OmgSettings) -> Path:
    home = cfg.sysml_home
    if " " in str(home):
        raise ParserUnavailable(
            f"sysml_home contient un espace ({home}) : le parseur OMG ne le supporte pas. "
            "Definir P9_SYSML_HOME vers un dossier sans espace.")
    return home


def _paths(cfg: OmgSettings) -> dict[str, Path]:
    home = _home(cfg)
    kdir = home / f"kernel-{cfg.kernel_version}"
    return {
        "home": home,
        "kernel_dir": kdir,
        "jar": kdir / f"jupyter-sysml-kernel-{cfg.kernel_version}-all.jar",
        "library": kdir / "library",
        "classes": home / "classes",
        "jdk": home / "jdk21",
    }


def _download(url: str, dest: Path) -> Path:
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"telechargement {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "p9-setup/0.1"})
    with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:  # noqa: S310 (URL de config)
        shutil.copyfileobj(r, f)
    tmp.replace(dest)
    return dest


def _install_kernel(cfg: OmgSettings, p: dict[str, Path]) -> None:
    if p["jar"].is_file() and (p["library"] / "Kernel_Libraries").is_dir():
        return
    archive = _download(cfg.kernel_url, p["home"] / "dl" / Path(cfg.kernel_url).name)
    p["kernel_dir"].mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            parts = info.filename.replace("\\", "/").split("/")
            if parts[-1].endswith("-all.jar"):
                p["jar"].write_bytes(z.read(info))
            elif "sysml.library" in parts:
                rel = parts[parts.index("sysml.library") + 1:]
                if not rel or rel[-1].startswith("."):
                    continue
                target = p["library"].joinpath(*[seg.replace(" ", "_") for seg in rel])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(info))


def _java_version(java: Path) -> int | None:
    try:
        out = subprocess.run([str(java), "-version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'version "(\d+)', out.stderr + out.stdout)
    return int(m.group(1)) if m else None


def _exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def _jdk_candidates(cfg: OmgSettings, p: dict[str, Path]) -> list[Path]:
    homes = [cfg.java_home, p["jdk"], Path(os.environ["JAVA_HOME"]) if os.environ.get("JAVA_HOME") else None]
    cands = [h for h in homes if h]
    which = shutil.which("java")
    if which:
        cands.append(Path(which).resolve().parent.parent)
    return cands


def find_jdk(cfg: OmgSettings, p: dict[str, Path]) -> Path | None:
    """Un JDK (java >= 21 + javac) parmi P9_JAVA_HOME, le JDK installé par setup, JAVA_HOME, PATH."""
    for home in _jdk_candidates(cfg, p):
        java, javac = home / "bin" / _exe("java"), home / "bin" / _exe("javac")
        if java.is_file() and javac.is_file() and (_java_version(java) or 0) >= 21:
            return home
    return None


def _install_jdk(cfg: OmgSettings, p: dict[str, Path]) -> Path:
    os_name = {"Windows": "windows", "Linux": "linux", "Darwin": "mac"}[platform.system()]
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "aarch64", "aarch64": "aarch64"}[platform.machine().lower()]
    url = cfg.jdk_url_template.format(os=os_name, arch=arch)
    ext = ".zip" if os_name == "windows" else ".tar.gz"
    archive = _download(url, p["home"] / "dl" / f"jdk21-{os_name}-{arch}{ext}")
    tmp = p["home"] / "jdk-extract"
    shutil.rmtree(tmp, ignore_errors=True)
    if ext == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
    else:
        import tarfile
        with tarfile.open(archive) as t:
            t.extractall(tmp, filter="data")
    root = next(d for d in tmp.iterdir() if d.is_dir())
    if (root / "Contents" / "Home").is_dir():          # macOS
        root = root / "Contents" / "Home"
    shutil.rmtree(p["jdk"], ignore_errors=True)
    shutil.move(str(root), str(p["jdk"]))
    shutil.rmtree(tmp, ignore_errors=True)
    return p["jdk"]


def _helper_source(root: Path) -> Path:
    return root / "tools" / "sysml" / f"{JAVA_HELPER}.java"


def setup(verbose: bool = True) -> dict[str, Path]:
    s = load_settings()
    cfg = s.omg
    assert cfg is not None
    p = _paths(cfg)
    _install_kernel(cfg, p)
    jdk = find_jdk(cfg, p) or _install_jdk(cfg, p)
    src = _helper_source(s.root)
    cls = p["classes"] / f"{JAVA_HELPER}.class"
    if not cls.is_file() or cls.stat().st_mtime < src.stat().st_mtime:
        p["classes"].mkdir(parents=True, exist_ok=True)
        subprocess.run([str(jdk / "bin" / _exe("javac")), "--release", "11", "-d", str(p["classes"]), str(src)],
                       check=True)
    p["java"] = jdk / "bin" / _exe("java")
    if verbose:
        print(f"parseur OMG pret : {p['jar']}\njava : {p['java']}")
    return p


def is_installed() -> bool:
    try:
        s = load_settings()
        assert s.omg is not None
        p = _paths(s.omg)
        return (p["jar"].is_file() and (p["classes"] / f"{JAVA_HELPER}.class").is_file()
                and find_jdk(s.omg, p) is not None)
    except (ParserUnavailable, OSError):
        return False


# --------------------------------------------------------------------- validation

_IMPORT = re.compile(r"^\s*(?:private\s+|public\s+)?import\s+([A-Za-z_][A-Za-z0-9_]*)::", re.M)
_PACKAGE = re.compile(r"^package\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)


def order_by_imports(texts: dict[str, str]) -> list[str]:
    """Trie les fichiers pour qu'un package importé soit traité avant ceux qui l'importent."""
    provides = {name: set(_PACKAGE.findall(t)) for name, t in texts.items()}
    needs = {name: set(_IMPORT.findall(t)) - provides[name] for name, t in texts.items()}
    done: list[str] = []
    pending = sorted(texts)
    while pending:
        ready = [n for n in pending if all(any(dep in provides[d] for d in done) or
                                           not any(dep in provides[o] for o in texts) for dep in needs[n])]
        if not ready:          # cycle : on garde l'ordre alphabétique
            ready = pending[:1]
        for n in ready:
            done.append(n)
            pending.remove(n)
    return done


def validate_files(paths: list[Path], timeout: int = 600) -> ValidationResult:
    if not paths:
        return ValidationResult(returncode=2, stderr="aucun fichier")
    if not is_installed():
        raise ParserUnavailable("parseur OMG absent : lancer `make validate-setup`")
    p = setup(verbose=False)
    texts = {str(pth): pth.read_text(encoding="utf-8") for pth in paths}
    ordered = order_by_imports(texts)
    cp = os.pathsep.join([str(p["jar"]), str(p["classes"])])
    proc = subprocess.run([str(p["java"]), "-cp", cp, JAVA_HELPER, str(p["library"]), *ordered],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    res = ValidationResult(returncode=proc.returncode,
                           stderr="\n".join(line for line in proc.stderr.splitlines() if "log4j" not in line))
    for line in proc.stdout.splitlines():
        parts = line.split("|", 5)
        if parts[0] == "ISSUE" and len(parts) == 6:
            res.issues.append(SysmlIssue(file=parts[1], severity=parts[2], line=int(parts[3]),
                                         column=int(parts[4]), message=parts[5]))
        elif parts[0] == "FILE" and len(parts) == 4:
            res.files.append(FileResult(file=parts[1], errors=int(parts[2]), warnings=int(parts[3])))
    return res


def validate_texts(files: dict[str, str], workdir: Path) -> ValidationResult:
    """Valide des contenus en mémoire (écrits dans workdir)."""
    workdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in files.items():
        pth = workdir / name
        pth.write_bytes(text.encode("utf-8"))
        paths.append(pth)
    return validate_files(paths)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"setup", "validate", "validate-out"}:
        print(__doc__)
        return 2
    if argv[0] == "setup":
        setup()
        return 0
    groups: list[list[Path]] = []
    if argv[0] == "validate-out":
        out = load_settings().out_dir
        groups = [sorted((out / "sysml").glob("*.sysml")), [out / "model.sysml"]]
        groups = [[p for p in g if p.is_file()] for g in groups]
        if not any(groups):
            print(f"Aucun .sysml dans {out} : lancer `make generate` d'abord.")
            return 2
    else:
        current: list[Path] = []
        for a in argv[1:]:
            if a == "--":                      # sépare des sessions indépendantes
                groups.append(current)
                current = []
            else:
                current.append(Path(a))
        groups.append(current)
    code = 0
    for g in (g for g in groups if g):
        try:
            res = validate_files(g)
        except ParserUnavailable as e:
            print(f"Erreur : {e}")
            return 2
        for i in res.issues:
            print(f"{i.severity:<7} {i.file}:{i.line}:{i.column} {i.message}")
        for f in res.files:
            print(f"{'OK ' if f.errors == 0 else 'KO '} {f.file} ({f.errors} erreur(s), {f.warnings} warning(s))")
        if res.stderr.strip() and not res.files:
            print(res.stderr)
        code = code or (0 if res.ok else 1)
    print("Validation OMG OK" if code == 0 else "Validation OMG : erreurs")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
