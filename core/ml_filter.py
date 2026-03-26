"""
ML Signal Filter — filtre predictif pour skipper les trades a faible probabilite.

IMPORTANT : NE PAS UTILISER avant d'avoir >= 200 trades live par strategie.
Un modele entraine sur trop peu de donnees va overfitter et degrader la performance.

Architecture :
  1. Features extraites en temps reel (heure, VIX, regime, volume, etc.)
  2. LightGBM avec forte regularisation (prevent overfitting)
  3. Walk-forward validation (pas de lookahead)
  4. Seuil conservateur : skip si P(profitable) < 0.4

Usage futur (pas avant J+240 / Live L3) :
    ml_filter = MLSignalFilter(min_trades_required=200)

    # Entrainement sur les trades historiques
    trades_df = load_trades("gap_continuation")
    metrics = ml_filter.train(trades_df, "gap_continuation")
    print(metrics)  # accuracy, precision, recall, auc

    # Prediction en temps reel
    features = extract_features(current_bar, regime, vix)
    if ml_filter.should_trade(features):
        execute_trade(...)
    else:
        logger.info("Trade skipped par ML filter")
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Check si LightGBM est disponible (optionnel pour l'instant)
try:
    import lightgbm as lgb
    _HAS_LGBM = True
except ImportError:
    _HAS_LGBM = False
    logger.info("LightGBM non installe — ML filter desactive (pip install lightgbm)")

# Check pandas
try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False


class MLSignalFilter:
    """Filtre ML pour skipper les trades a faible probabilite.

    NE PAS UTILISER avant d'avoir 200+ trades live par strategie.
    """

    # Features utilisees pour la prediction
    FEATURES = [
        'hour_of_day',       # Heure de la journee (9-16 ET)
        'day_of_week',       # Jour de la semaine (0=lundi, 4=vendredi)
        'vix_level',         # Niveau du VIX au moment du signal
        'regime',            # Regime de marche (0=bear, 1=neutral, 2=bull)
        'gap_pct',           # % de gap a l'ouverture
        'volume_ratio',      # Volume courant / volume moyen 20j
        'atr_ratio',         # ATR courant / ATR moyen 20j
        'spy_return_1h',     # Return SPY sur la derniere heure
        'rsi_14',            # RSI 14 periodes
        'bb_position',       # Position dans les Bollinger Bands (0-1)
        'spread_pct',        # Spread bid-ask en % du prix
        'sector_momentum',   # Momentum du secteur sur 5 jours
    ]

    # Hyper-parametres LightGBM avec forte regularisation
    LGBM_PARAMS = {
        'objective': 'binary',
        'metric': 'auc',
        'boosting_type': 'gbdt',
        'num_leaves': 15,          # Peu de feuilles → moins d'overfitting
        'max_depth': 4,            # Profondeur limitee
        'learning_rate': 0.05,     # Apprentissage lent
        'n_estimators': 200,       # Nombre d'arbres modere
        'min_child_samples': 20,   # Minimum d'echantillons par feuille
        'reg_alpha': 1.0,          # Regularisation L1 forte
        'reg_lambda': 2.0,         # Regularisation L2 forte
        'subsample': 0.8,          # Bagging
        'colsample_bytree': 0.7,   # Feature sampling
        'verbose': -1,
    }

    # Seuil par defaut pour skipper un trade
    DEFAULT_THRESHOLD = 0.4

    def __init__(self, min_trades_required: int = 200,
                 model_dir: Optional[str] = None):
        """
        Args:
            min_trades_required: nombre minimum de trades pour l'entrainement.
            model_dir: repertoire de sauvegarde des modeles entraines.
        """
        self.min_trades = min_trades_required
        self.model = None
        self.strategy_name = None
        self.feature_importance = None
        self.metrics = None

        if model_dir is None:
            self.model_dir = Path(__file__).parent.parent / "data_cache" / "ml_models"
        else:
            self.model_dir = Path(model_dir)

    def is_ready(self) -> bool:
        """Verifie si le modele est entraine et pret a predire."""
        return self.model is not None

    def train(self, trades_df: Any, strategy_name: str) -> dict:
        """Entraine un LightGBM sur les trades historiques.

        Utilise une validation walk-forward (pas de split random) :
          - 70% premiers trades = train
          - 30% derniers trades = test

        Args:
            trades_df: DataFrame pandas avec colonnes FEATURES + 'profitable' (0/1).
            strategy_name: nom de la strategie.

        Returns:
            {
                accuracy: float,
                precision: float,
                recall: float,
                auc: float,
                f1: float,
                feature_importance: {feature: importance},
                train_size: int,
                test_size: int,
            }

        Raises:
            ValueError: si pas assez de trades.
            ImportError: si LightGBM ou pandas non installe.
        """
        if not _HAS_LGBM:
            raise ImportError(
                "LightGBM requis pour l'entrainement. "
                "Installer avec : pip install lightgbm"
            )
        if not _HAS_PANDAS:
            raise ImportError(
                "pandas requis pour l'entrainement. "
                "Installer avec : pip install pandas"
            )

        if len(trades_df) < self.min_trades:
            raise ValueError(
                f"Pas assez de trades ({len(trades_df)} < {self.min_trades}). "
                f"Attendre d'avoir au moins {self.min_trades} trades live."
            )

        self.strategy_name = strategy_name

        # Verifier que les features sont presentes
        available_features = [f for f in self.FEATURES if f in trades_df.columns]
        if len(available_features) < 3:
            raise ValueError(
                f"Trop peu de features disponibles ({available_features}). "
                f"Attendues : {self.FEATURES}"
            )

        X = trades_df[available_features].values
        y = trades_df['profitable'].values.astype(int)

        # Walk-forward split (70/30 chronologique — PAS random)
        split_idx = int(len(X) * 0.7)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        # Entrainement
        train_data = lgb.Dataset(X_train, label=y_train,
                                  feature_name=available_features)
        valid_data = lgb.Dataset(X_test, label=y_test,
                                  feature_name=available_features, reference=train_data)

        callbacks = [lgb.early_stopping(stopping_rounds=20, verbose=False)]

        self.model = lgb.train(
            self.LGBM_PARAMS,
            train_data,
            valid_sets=[valid_data],
            callbacks=callbacks,
        )

        # Predictions sur le test set
        y_pred_proba = self.model.predict(X_test)
        y_pred = (y_pred_proba >= self.DEFAULT_THRESHOLD).astype(int)

        # Metriques
        accuracy = float(np.mean(y_pred == y_test))
        tp = np.sum((y_pred == 1) & (y_test == 1))
        fp = np.sum((y_pred == 1) & (y_test == 0))
        fn = np.sum((y_pred == 0) & (y_test == 1))
        precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        recall = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)
               if (precision + recall) > 0 else 0.0)

        # AUC approximation
        auc = self._compute_auc(y_test, y_pred_proba)

        # Feature importance
        importance = dict(zip(
            available_features,
            self.model.feature_importance(importance_type='gain').tolist()
        ))
        self.feature_importance = importance

        self.metrics = {
            "accuracy": round(accuracy, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "auc": round(auc, 4),
            "f1": round(f1, 4),
            "feature_importance": {k: round(v, 2) for k, v in importance.items()},
            "train_size": len(X_train),
            "test_size": len(X_test),
            "features_used": available_features,
        }

        logger.info(
            f"ML filter entraine pour {strategy_name}: "
            f"AUC={auc:.4f}, precision={precision:.4f}, recall={recall:.4f}"
        )

        return self.metrics

    def predict(self, features: dict) -> float:
        """Retourne P(profitable) pour un set de features.

        Args:
            features: dict avec les cles de FEATURES.

        Returns:
            Probabilite entre 0 et 1. Si < DEFAULT_THRESHOLD → skip le trade.

        Raises:
            RuntimeError: si le modele n'est pas entraine.
        """
        if self.model is None:
            raise RuntimeError(
                "Modele non entraine. Appeler train() d'abord, "
                f"avec au moins {self.min_trades} trades."
            )

        # Construire le vecteur de features dans le bon ordre
        feature_vector = []
        for f in self.FEATURES:
            val = features.get(f, 0.0)
            feature_vector.append(float(val) if val is not None else 0.0)

        proba = self.model.predict([feature_vector])[0]
        return float(proba)

    def should_trade(self, features: dict,
                     threshold: Optional[float] = None) -> bool:
        """Decide si on doit prendre le trade.

        Args:
            features: dict avec les cles de FEATURES.
            threshold: seuil de probabilite (defaut: DEFAULT_THRESHOLD).

        Returns:
            True si P(profitable) >= threshold, False sinon.
        """
        if threshold is None:
            threshold = self.DEFAULT_THRESHOLD

        if self.model is None:
            # Si pas de modele, on ne filtre pas (fail open)
            logger.debug("ML filter non entraine — trade autorise par defaut")
            return True

        proba = self.predict(features)
        decision = proba >= threshold

        if not decision:
            logger.info(
                f"ML filter SKIP: P(profitable)={proba:.3f} < {threshold:.2f} "
                f"({self.strategy_name})"
            )

        return decision

    def save_model(self, filepath: Optional[str] = None) -> str:
        """Sauvegarde le modele entraine sur disque.

        Args:
            filepath: chemin du fichier (defaut: model_dir/strategy_name.lgbm).

        Returns:
            Chemin du fichier sauvegarde.
        """
        if self.model is None:
            raise RuntimeError("Aucun modele a sauvegarder.")

        if filepath is None:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            filepath = str(self.model_dir / f"{self.strategy_name}.lgbm")

        self.model.save_model(filepath)
        logger.info(f"Modele sauvegarde : {filepath}")
        return filepath

    def load_model(self, filepath: str, strategy_name: str) -> None:
        """Charge un modele depuis le disque.

        Args:
            filepath: chemin du fichier .lgbm.
            strategy_name: nom de la strategie associee.
        """
        if not _HAS_LGBM:
            raise ImportError("LightGBM requis. pip install lightgbm")

        self.model = lgb.Booster(model_file=filepath)
        self.strategy_name = strategy_name
        logger.info(f"Modele charge : {filepath} ({strategy_name})")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_auc(y_true: np.ndarray, y_scores: np.ndarray) -> float:
        """Calcule l'AUC-ROC sans sklearn (implementation manuelle).

        Utilise l'algorithme de tri par score decroissant.
        """
        # Trier par score decroissant
        desc_indices = np.argsort(y_scores)[::-1]
        y_sorted = y_true[desc_indices]

        n_pos = np.sum(y_true == 1)
        n_neg = np.sum(y_true == 0)

        if n_pos == 0 or n_neg == 0:
            return 0.5

        tp = 0
        fp = 0
        auc = 0.0
        prev_fp = 0

        for i in range(len(y_sorted)):
            if y_sorted[i] == 1:
                tp += 1
            else:
                fp += 1
                auc += tp  # Nombre de positifs vus avant ce faux positif

        auc /= (n_pos * n_neg)
        return float(auc)
