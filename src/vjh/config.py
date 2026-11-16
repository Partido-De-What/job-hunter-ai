"""Load and validate configuration. Secrets come only from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .models.profile import HomeLocation, MasterProfile
from .models.settings import Settings


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from `start` (or cwd) to the folder containing config/settings.yaml."""
    env = os.environ.get("VJH_HOME")
    if env:
        return Path(env).resolve()
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / "config" / "settings.yaml").is_file():
            return folder
    raise FileNotFoundError(
        "Could not find config/settings.yaml. Run vjh from inside the project folder "
        "or set VJH_HOME."
    )


def _expand(path: str, root: Path) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    return p if p.is_absolute() else (root / p)


@dataclass(frozen=True)
class AppConfig:
    root: Path
    settings: Settings

    @property
    def db_path(self) -> Path:
        override = os.environ.get("VJH_DB_PATH")
        return _expand(override or self.settings.database.path, self.root)

    @property
    def data_dir(self) -> Path:
        return _expand(self.settings.paths.data_dir, self.root)

    @property
    def personal_dir(self) -> Path:
        return _expand(self.settings.paths.personal_dir, self.root)

    @property
    def profile_path(self) -> Path:
        return self.personal_dir / "master_profile.yaml"

    @property
    def home_location_path(self) -> Path:
        return self.personal_dir / "home_location.yaml"


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a YAML mapping")
    return data


def load_config(root: Path | None = None) -> AppConfig:
    root = root or find_project_root()
    load_dotenv(root / ".env", override=False)   # secrets → os.environ only
    settings = Settings.model_validate(load_yaml(root / "config" / "settings.yaml"))
    return AppConfig(root=root, settings=settings)


def load_profile(path: Path) -> MasterProfile:
    return MasterProfile.model_validate(load_yaml(path))


def load_home_location(path: Path) -> HomeLocation:
    return HomeLocation.model_validate(load_yaml(path))


def secret_present(name: str) -> bool:
    """Report whether a secret is set without ever returning its value."""
    return bool(os.environ.get(name))
