"""
Validation du JSON stratégie contre le schéma canonique.
Toute stratégie doit passer cette validation avant d'entrer dans le pipeline.
"""
import hashlib
import json
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).parent / "schema.json"


class StrategyValidationError(Exception):
    """Levée si le JSON stratégie ne valide pas le schéma."""
    pass


class StrategyValidator:
    """
    Valide et normalise un JSON de stratégie.

    Usage :
        validator = StrategyValidator()
        strategy = validator.load_and_validate("strategies/rsi_mean_reversion.json")
        fingerprint = validator.fingerprint(strategy)
    """

    def __init__(self):
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            self._schema = json.load(f)
        self._validator = jsonschema.Draft7Validator(self._schema)

    def validate(self, strategy: dict) -> dict:
        """
        Valide un dict stratégie. Lève StrategyValidationError si invalide.
        Retourne la stratégie enrichie d'un champ _fingerprint.
        """
        errors = list(self._validator.iter_errors(strategy))
        if errors:
            messages = [f"  - {e.json_path}: {e.message}" for e in errors]
            raise StrategyValidationError(
                f"Stratégie invalide ({len(errors)} erreur(s)) :\n" + "\n".join(messages)
            )
        # Ajout du fingerprint pour reproductibilité
        strategy = dict(strategy)
        strategy["_fingerprint"] = self.fingerprint(strategy)
        return strategy

    def load_and_validate(self, path: str | Path) -> dict:
        """Charge un fichier JSON et le valide."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Fichier stratégie introuvable : {path}")
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return self.validate(raw)

    def fingerprint(self, strategy: dict) -> str:
        """
        Hash SHA256 déterministe du JSON stratégie (hors _fingerprint).
        Permet de vérifier qu'une stratégie n'a pas été modifiée entre
        le backtest et l'exécution.
        """
        clean = {k: v for k, v in strategy.items() if k != "_fingerprint"}
        canonical = json.dumps(clean, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()
