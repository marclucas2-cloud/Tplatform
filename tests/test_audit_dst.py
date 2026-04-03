"""
Tests pour AuditDST — validation des fuseaux horaires et transitions DST.

Couvre :
  - Horaires EU (ete/hiver) en UTC
  - Horaires US (ete/hiver) en UTC
  - Session FX (dimanche-vendredi)
  - Crypto 24/7
  - Detection des transitions DST
  - Alignement des bougies (candle alignment)
  - Synchronisation d'horloge
  - Cas limites (minuit, jour de transition DST, weekends)
"""

import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.data.audit_dst import (
    DST_WARNING_HOURS,
    MARKET_DEFINITIONS,
    AuditDST,
)

_UTC = UTC
_PARIS = ZoneInfo("Europe/Paris")
_NY = ZoneInfo("America/New_York")


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def audit_winter():
    """Audit avec date de reference en hiver (CET = UTC+1, EST = UTC-5)."""
    # 15 janvier 2025 — mercredi, hiver
    return AuditDST(reference_date=date(2025, 1, 15))


@pytest.fixture
def audit_summer():
    """Audit avec date de reference en ete (CEST = UTC+2, EDT = UTC-4)."""
    # 15 juillet 2025 — mardi, ete
    return AuditDST(reference_date=date(2025, 7, 15))


@pytest.fixture
def audit_dst_day_eu():
    """Audit le jour du passage a l'heure d'ete EU (dernier dimanche de mars)."""
    # 30 mars 2025 — dimanche, passage CET -> CEST
    return AuditDST(reference_date=date(2025, 3, 30))


@pytest.fixture
def audit_dst_day_us():
    """Audit le jour du passage a l'heure d'ete US (2e dimanche de mars)."""
    # 9 mars 2025 — dimanche, passage EST -> EDT
    return AuditDST(reference_date=date(2025, 3, 9))


# =============================================================================
# TESTS — HORAIRES DE MARCHE EU
# =============================================================================

class TestEUMarketHours:
    """Tests horaires EU (DAX/CAC/SX5E) : 09:00-17:30 CET."""

    def test_eu_winter_utc_offsets(self, audit_winter):
        """Hiver CET = UTC+1 : open 08:00 UTC, close 16:30 UTC."""
        open_utc, close_utc = audit_winter.get_market_calendar("EU", date(2025, 1, 15))
        assert open_utc is not None
        assert close_utc is not None
        assert open_utc.hour == 8
        assert open_utc.minute == 0
        assert close_utc.hour == 16
        assert close_utc.minute == 30

    def test_eu_summer_utc_offsets(self, audit_summer):
        """Ete CEST = UTC+2 : open 07:00 UTC, close 15:30 UTC."""
        open_utc, close_utc = audit_summer.get_market_calendar("EU", date(2025, 7, 15))
        assert open_utc is not None
        assert close_utc is not None
        assert open_utc.hour == 7
        assert open_utc.minute == 0
        assert close_utc.hour == 15
        assert close_utc.minute == 30

    def test_eu_weekend_closed(self, audit_winter):
        """EU ferme le weekend."""
        # samedi 18 janvier 2025
        open_utc, close_utc = audit_winter.get_market_calendar("EU", date(2025, 1, 18))
        assert open_utc is None
        assert close_utc is None

        # dimanche 19 janvier 2025
        open_utc, close_utc = audit_winter.get_market_calendar("EU", date(2025, 1, 19))
        assert open_utc is None
        assert close_utc is None

    def test_eu_open_before_close(self, audit_winter):
        """L'heure d'ouverture doit etre avant la fermeture."""
        open_utc, close_utc = audit_winter.get_market_calendar("EU", date(2025, 1, 15))
        assert open_utc < close_utc

    def test_eu_check_market_hours_pass(self, audit_winter):
        """check_market_hours retourne pass=True pour EU un jour ouvre."""
        result = audit_winter.check_market_hours("EU")
        assert result["pass"] is True
        assert result["is_open_today"] is True
        assert result["market"] == "EU"


# =============================================================================
# TESTS — HORAIRES DE MARCHE US
# =============================================================================

class TestUSMarketHours:
    """Tests horaires US (SPY/QQQ) : 09:30-16:00 ET."""

    def test_us_winter_utc_offsets(self, audit_winter):
        """Hiver EST = UTC-5 : open 14:30 UTC, close 21:00 UTC."""
        open_utc, close_utc = audit_winter.get_market_calendar("US", date(2025, 1, 15))
        assert open_utc is not None
        assert close_utc is not None
        assert open_utc.hour == 14
        assert open_utc.minute == 30
        assert close_utc.hour == 21
        assert close_utc.minute == 0

    def test_us_summer_utc_offsets(self, audit_summer):
        """Ete EDT = UTC-4 : open 13:30 UTC, close 20:00 UTC."""
        open_utc, close_utc = audit_summer.get_market_calendar("US", date(2025, 7, 15))
        assert open_utc is not None
        assert close_utc is not None
        assert open_utc.hour == 13
        assert open_utc.minute == 30
        assert close_utc.hour == 20
        assert close_utc.minute == 0

    def test_us_weekend_closed(self, audit_summer):
        """US ferme le weekend."""
        # samedi 19 juillet 2025
        open_utc, close_utc = audit_summer.get_market_calendar("US", date(2025, 7, 19))
        assert open_utc is None
        assert close_utc is None

    def test_us_open_before_close(self, audit_summer):
        """L'heure d'ouverture doit etre avant la fermeture."""
        open_utc, close_utc = audit_summer.get_market_calendar("US", date(2025, 7, 15))
        assert open_utc < close_utc

    def test_us_check_market_hours_pass(self, audit_winter):
        """check_market_hours retourne pass=True pour US un jour ouvre."""
        result = audit_winter.check_market_hours("US")
        assert result["pass"] is True
        assert result["is_open_today"] is True


# =============================================================================
# TESTS — SESSION FX
# =============================================================================

class TestFXSession:
    """Tests session FX : dimanche 17:00 ET -> vendredi 17:00 ET."""

    def test_fx_weekday_session(self, audit_winter):
        """Un jour de semaine a une session 17:00 ET veille -> 17:00 ET jour."""
        # Mercredi 15 janvier 2025
        open_utc, close_utc = audit_winter.get_market_calendar("FX", date(2025, 1, 15))
        assert open_utc is not None
        assert close_utc is not None
        # 17:00 ET (hiver EST = UTC-5) = 22:00 UTC
        assert open_utc.hour == 22  # veille = mardi 22:00 UTC
        assert open_utc.day == 14   # mardi
        assert close_utc.hour == 22  # mercredi 22:00 UTC
        assert close_utc.day == 15

    def test_fx_saturday_closed(self, audit_winter):
        """FX ferme le samedi."""
        open_utc, close_utc = audit_winter.get_market_calendar("FX", date(2025, 1, 18))
        assert open_utc is None
        assert close_utc is None

    def test_fx_sunday_opens(self, audit_winter):
        """FX ouvre le dimanche a 17:00 ET."""
        # Dimanche 19 janvier 2025
        open_utc, close_utc = audit_winter.get_market_calendar("FX", date(2025, 1, 19))
        assert open_utc is not None
        assert close_utc is not None
        # Dimanche 17:00 ET (EST UTC-5) = dimanche 22:00 UTC
        assert open_utc.hour == 22
        assert open_utc.weekday() == 6  # dimanche

    def test_fx_summer_utc_shift(self, audit_summer):
        """En ete EDT = UTC-4 : 17:00 ET = 21:00 UTC."""
        open_utc, close_utc = audit_summer.get_market_calendar("FX", date(2025, 7, 15))
        assert open_utc is not None
        # 17:00 ET (ete EDT = UTC-4) = 21:00 UTC
        assert open_utc.hour == 21
        assert close_utc.hour == 21

    def test_fx_is_open_weekday(self, audit_winter):
        """FX est ouvert un jour de semaine."""
        result = audit_winter.check_market_hours("FX")
        assert result["is_open_today"] is True

    def test_fx_open_before_close(self, audit_winter):
        """La session FX doit avoir open < close."""
        open_utc, close_utc = audit_winter.get_market_calendar("FX", date(2025, 1, 15))
        assert open_utc < close_utc


# =============================================================================
# TESTS — CRYPTO 24/7
# =============================================================================

class TestCryptoHours:
    """Tests crypto : 24/7 en UTC."""

    def test_crypto_always_open(self, audit_winter):
        """Crypto ouvert tous les jours, y compris weekend."""
        for day_offset in range(7):
            d = date(2025, 1, 13) + timedelta(days=day_offset)
            open_utc, close_utc = audit_winter.get_market_calendar("CRYPTO", d)
            assert open_utc is not None, f"Crypto ferme le {d}"
            assert close_utc is not None, f"Crypto ferme le {d}"

    def test_crypto_full_day_utc(self, audit_winter):
        """Crypto couvre 00:00 - 23:59:59 UTC."""
        open_utc, close_utc = audit_winter.get_market_calendar("CRYPTO", date(2025, 1, 15))
        assert open_utc.hour == 0
        assert open_utc.minute == 0
        assert close_utc.hour == 23
        assert close_utc.minute == 59
        assert close_utc.second == 59

    def test_crypto_check_market_hours_pass(self, audit_winter):
        """check_market_hours retourne pass=True pour crypto."""
        result = audit_winter.check_market_hours("CRYPTO")
        assert result["pass"] is True
        assert result["is_open_today"] is True

    def test_crypto_maintenance_defined(self):
        """La maintenance Binance est definie dans MARKET_DEFINITIONS."""
        crypto_def = MARKET_DEFINITIONS["CRYPTO"]
        assert crypto_def["maintenance_day"] == 1  # mardi
        assert crypto_def["maintenance_hour_utc"] == 6


# =============================================================================
# TESTS — DETECTION TRANSITIONS DST
# =============================================================================

class TestDSTTransitions:
    """Tests de detection des transitions DST."""

    def test_detect_us_spring_forward(self):
        """Detecte le passage US spring forward (2e dimanche de mars)."""
        # Recherche a partir du 1er janvier 2025
        audit = AuditDST(reference_date=date(2025, 1, 1))
        result = audit.check_dst_transitions()

        assert result["next_us"] is not None
        # US spring forward 2025 = 9 mars
        assert result["next_us"] == "2025-03-09"

    def test_detect_eu_spring_forward(self):
        """Detecte le passage EU spring forward (dernier dimanche de mars)."""
        audit = AuditDST(reference_date=date(2025, 1, 1))
        result = audit.check_dst_transitions()

        assert result["next_eu"] is not None
        # EU spring forward 2025 = 30 mars
        assert result["next_eu"] == "2025-03-30"

    def test_detect_us_fall_back(self):
        """Detecte le passage US fall back (1er dimanche de novembre)."""
        # Recherche a partir du 1er septembre 2025
        audit = AuditDST(reference_date=date(2025, 9, 1))
        result = audit.check_dst_transitions()

        assert result["next_us"] is not None
        # US fall back 2025 = 2 novembre
        assert result["next_us"] == "2025-11-02"

    def test_detect_eu_fall_back(self):
        """Detecte le passage EU fall back (dernier dimanche d'octobre)."""
        audit = AuditDST(reference_date=date(2025, 9, 1))
        result = audit.check_dst_transitions()

        assert result["next_eu"] is not None
        # EU fall back 2025 = 26 octobre
        assert result["next_eu"] == "2025-10-26"

    def test_no_warning_when_far(self):
        """Pas de warning si la transition est loin (> 48h)."""
        # 1er janvier = ~67 jours avant la transition US
        audit = AuditDST(reference_date=date(2025, 1, 1))
        result = audit.check_dst_transitions()

        assert result["pass"] is True
        assert len(result["warnings"]) == 0

    def test_transitions_have_offset_info(self):
        """Les transitions contiennent les offsets avant/apres."""
        audit = AuditDST(reference_date=date(2025, 1, 1))
        result = audit.check_dst_transitions()

        for trans in result["transitions"]:
            assert "from_offset" in trans
            assert "to_offset" in trans
            assert "zone" in trans
            assert "date" in trans
            assert "hours_away" in trans

    def test_us_eu_gap_period(self):
        """Entre le DST US (9 mars) et EU (30 mars) les offsets divergent.

        Pendant cette periode, le decalage US-EU est different de d'habitude.
        """
        # 15 mars 2025 : US en EDT (UTC-4), EU encore en CET (UTC+1)
        d = date(2025, 3, 15)
        ny_dt = datetime.combine(d, time(12, 0), tzinfo=_NY)
        paris_dt = datetime.combine(d, time(12, 0), tzinfo=_PARIS)

        ny_offset = ny_dt.utcoffset()
        paris_offset = paris_dt.utcoffset()

        # EDT = -4h, CET = +1h, donc difference = 5h
        diff_hours = (paris_offset - ny_offset).total_seconds() / 3600
        assert diff_hours == 5.0  # normalement 6h en ete, 5h pendant le gap


# =============================================================================
# TESTS — ALIGNEMENT DES BOUGIES (CANDLE ALIGNMENT)
# =============================================================================

class TestCandleAlignment:
    """Tests de validation de l'alignement des bougies."""

    def test_valid_utc_candles(self, audit_winter):
        """Bougies avec DatetimeIndex UTC aware passent la validation."""
        idx = pd.date_range("2025-01-15 09:00", periods=10, freq="1h", tz="UTC")
        df = pd.DataFrame({"close": range(10)}, index=idx)

        result = audit_winter.check_candle_alignment(df, "UTC")
        assert result["pass"] is True
        assert result["total_candles"] == 10
        assert result["tz_match"] is True

    def test_naive_utc_accepted(self, audit_winter):
        """Bougies naives acceptees comme UTC par convention."""
        idx = pd.date_range("2025-01-15 09:00", periods=10, freq="1h")
        df = pd.DataFrame({"close": range(10)}, index=idx)

        result = audit_winter.check_candle_alignment(df, "UTC")
        assert result["pass"] is True
        assert "naif" in result["anomalies"][0]

    def test_timezone_mismatch(self, audit_winter):
        """Mismatch entre tz des bougies et tz attendu."""
        # Bougies en UTC, mais on attend Europe/Paris
        idx = pd.date_range("2025-01-15 09:00", periods=10, freq="1h", tz="UTC")
        df = pd.DataFrame({"close": range(10)}, index=idx)

        result = audit_winter.check_candle_alignment(df, "Europe/Paris")
        assert result["pass"] is False
        assert result["tz_match"] is False

    def test_empty_dataframe(self, audit_winter):
        """DataFrame vide retourne pass=False."""
        df = pd.DataFrame()
        result = audit_winter.check_candle_alignment(df, "UTC")
        assert result["pass"] is False
        assert result["total_candles"] == 0

    def test_none_dataframe(self, audit_winter):
        """None retourne pass=False."""
        result = audit_winter.check_candle_alignment(None, "UTC")
        assert result["pass"] is False

    def test_non_datetime_index(self, audit_winter):
        """Index non DatetimeIndex retourne pass=False."""
        df = pd.DataFrame({"close": [1, 2, 3]}, index=[0, 1, 2])
        result = audit_winter.check_candle_alignment(df, "UTC")
        assert result["pass"] is False
        assert "DatetimeIndex" in result["anomalies"][0]

    def test_duplicate_timestamps_detected(self, audit_winter):
        """Les timestamps dupliques sont detectes."""
        idx = pd.DatetimeIndex([
            "2025-01-15 09:00",
            "2025-01-15 09:00",  # doublon
            "2025-01-15 10:00",
            "2025-01-15 11:00",
        ], tz="UTC")
        df = pd.DataFrame({"close": [1, 2, 3, 4]}, index=idx)

        result = audit_winter.check_candle_alignment(df, "UTC")
        assert result["pass"] is False
        has_dup_warning = any("dupliques" in a for a in result["anomalies"])
        assert has_dup_warning

    def test_non_monotonic_detected(self, audit_winter):
        """Index non monotone est detecte."""
        idx = pd.DatetimeIndex([
            "2025-01-15 10:00",
            "2025-01-15 09:00",  # desordre
            "2025-01-15 11:00",
        ], tz="UTC")
        df = pd.DataFrame({"close": [1, 2, 3]}, index=idx)

        result = audit_winter.check_candle_alignment(df, "UTC")
        assert result["pass"] is False
        has_mono_warning = any("monotone" in a for a in result["anomalies"])
        assert has_mono_warning

    def test_large_gap_detected(self, audit_winter):
        """Les gaps anormaux dans les bougies sont detectes."""
        idx = pd.DatetimeIndex([
            "2025-01-15 09:00",
            "2025-01-15 10:00",
            "2025-01-15 11:00",
            # Gap de 5h au lieu de 1h
            "2025-01-15 16:00",
            "2025-01-15 17:00",
        ], tz="UTC")
        df = pd.DataFrame({"close": range(5)}, index=idx)

        result = audit_winter.check_candle_alignment(df, "UTC")
        has_gap_warning = any("gaps" in a for a in result["anomalies"])
        assert has_gap_warning


# =============================================================================
# TESTS — SYNCHRONISATION HORLOGE
# =============================================================================

class TestBrokerClockSync:
    """Tests de la synchronisation d'horloge."""

    def test_clock_sync_pass(self, audit_winter):
        """L'horloge locale doit etre synchronisee (drift < 1s)."""
        result = audit_winter.check_broker_clock_sync()
        assert result["pass"] is True
        assert result["drift_ms"] < 1000
        assert "system_utc" in result
        assert "reference_utc" in result

    def test_clock_sync_has_details(self, audit_winter):
        """Le resultat contient un message de details."""
        result = audit_winter.check_broker_clock_sync()
        assert "details" in result
        assert isinstance(result["details"], str)


# =============================================================================
# TESTS — CHECK_ALL (INTEGRATION)
# =============================================================================

class TestCheckAll:
    """Tests d'integration de check_all()."""

    def test_check_all_structure(self, audit_winter):
        """check_all retourne la structure complete."""
        result = audit_winter.check_all()

        assert "timestamp" in result
        assert "reference_date" in result
        assert "checks" in result
        assert "overall_pass" in result

        checks = result["checks"]
        assert "market_hours" in checks
        assert "dst_transitions" in checks
        assert "broker_clock_sync" in checks

        # Tous les marches sont couverts
        for market in ["EU", "US", "FX", "CRYPTO"]:
            assert market in checks["market_hours"]

    def test_check_all_passes_on_weekday(self, audit_winter):
        """check_all passe un jour ouvre normal."""
        result = audit_winter.check_all()
        assert result["overall_pass"] is True

    def test_check_all_with_summer_date(self, audit_summer):
        """check_all fonctionne aussi en ete."""
        result = audit_summer.check_all()
        assert result["overall_pass"] is True

    def test_unknown_market(self, audit_winter):
        """Un marche inconnu retourne pass=False."""
        result = audit_winter.check_market_hours("UNKNOWN")
        assert result["pass"] is False
        assert "inconnu" in result["details"].lower() or "inconnu" in result["details"]

    def test_invalid_market_calendar(self, audit_winter):
        """get_market_calendar leve ValueError pour un marche inconnu."""
        with pytest.raises(ValueError, match="inconnu"):
            audit_winter.get_market_calendar("INVALID", date(2025, 1, 15))


# =============================================================================
# TESTS — CAS LIMITES
# =============================================================================

class TestEdgeCases:
    """Tests des cas limites : minuit, jour de transition DST, etc."""

    def test_midnight_utc_crypto(self):
        """Crypto a minuit UTC est ouvert."""
        audit = AuditDST(reference_date=date(2025, 1, 1))
        open_utc, close_utc = audit.get_market_calendar("CRYPTO", date(2025, 1, 1))
        assert open_utc.hour == 0
        assert open_utc.minute == 0

    def test_dst_change_day_eu_market_hours(self, audit_dst_day_eu):
        """Le jour de la transition DST EU, les marches EU sont fermes (dimanche)."""
        open_utc, close_utc = audit_dst_day_eu.get_market_calendar(
            "EU", date(2025, 3, 30)
        )
        assert open_utc is None  # Dimanche = ferme
        assert close_utc is None

    def test_dst_change_day_us_market_hours(self, audit_dst_day_us):
        """Le jour de la transition DST US, les marches US sont fermes (dimanche)."""
        open_utc, close_utc = audit_dst_day_us.get_market_calendar(
            "US", date(2025, 3, 9)
        )
        assert open_utc is None  # Dimanche = ferme
        assert close_utc is None

    def test_monday_after_us_dst(self):
        """Le lundi apres le changement DST US, les horaires UTC changent."""
        audit = AuditDST(reference_date=date(2025, 3, 10))

        # Lundi 10 mars 2025 : US en EDT (UTC-4)
        open_utc, close_utc = audit.get_market_calendar("US", date(2025, 3, 10))
        assert open_utc is not None
        # EDT: 09:30 ET = 13:30 UTC
        assert open_utc.hour == 13
        assert open_utc.minute == 30
        # EDT: 16:00 ET = 20:00 UTC
        assert close_utc.hour == 20
        assert close_utc.minute == 0

    def test_friday_before_us_dst(self):
        """Le vendredi avant le changement DST US, on est encore en EST."""
        audit = AuditDST(reference_date=date(2025, 3, 7))

        open_utc, close_utc = audit.get_market_calendar("US", date(2025, 3, 7))
        assert open_utc is not None
        # EST: 09:30 ET = 14:30 UTC
        assert open_utc.hour == 14
        assert open_utc.minute == 30
        # EST: 16:00 ET = 21:00 UTC
        assert close_utc.hour == 21

    def test_monday_after_eu_dst(self):
        """Le lundi apres le changement DST EU, les horaires UTC changent."""
        audit = AuditDST(reference_date=date(2025, 3, 31))

        open_utc, close_utc = audit.get_market_calendar("EU", date(2025, 3, 31))
        assert open_utc is not None
        # CEST: 09:00 CET = 07:00 UTC
        assert open_utc.hour == 7
        assert open_utc.minute == 0
        # CEST: 17:30 CET = 15:30 UTC
        assert close_utc.hour == 15
        assert close_utc.minute == 30

    def test_friday_before_eu_dst(self):
        """Le vendredi avant le changement DST EU, on est encore en CET."""
        audit = AuditDST(reference_date=date(2025, 3, 28))

        open_utc, close_utc = audit.get_market_calendar("EU", date(2025, 3, 28))
        assert open_utc is not None
        # CET: 09:00 CET = 08:00 UTC
        assert open_utc.hour == 8
        assert open_utc.minute == 0
        # CET: 17:30 CET = 16:30 UTC
        assert close_utc.hour == 16
        assert close_utc.minute == 30

    def test_new_years_day_weekday(self):
        """1er janvier 2025 = mercredi. Les marches sont 'ouverts' par calendrier."""
        # Note: on ne gere pas les jours feries ici, seulement les weekends.
        # Les feries sont geres par un calendrier externe.
        audit = AuditDST(reference_date=date(2025, 1, 1))
        open_utc, close_utc = audit.get_market_calendar("EU", date(2025, 1, 1))
        # Mercredi = jour ouvre selon notre calendrier simple
        assert open_utc is not None

    def test_default_reference_date(self):
        """Sans date de reference, utilise la date du jour."""
        audit = AuditDST()
        today = datetime.now(_UTC).date()
        assert audit.reference_date == today

    def test_fx_friday_close(self):
        """Le FX ferme le vendredi a 17:00 ET."""
        audit = AuditDST(reference_date=date(2025, 1, 17))  # vendredi
        open_utc, close_utc = audit.get_market_calendar("FX", date(2025, 1, 17))
        assert close_utc is not None
        # Vendredi 17:00 EST = 22:00 UTC
        assert close_utc.hour == 22
        assert close_utc.weekday() == 4  # vendredi

    def test_market_definitions_complete(self):
        """Tous les marches attendus sont definis."""
        expected = {"EU", "US", "FX", "CRYPTO"}
        assert set(MARKET_DEFINITIONS.keys()) == expected

    def test_dst_warning_hours_constant(self):
        """La constante DST_WARNING_HOURS est a 48h."""
        assert DST_WARNING_HOURS == 48

    def test_candle_alignment_paris_tz(self):
        """Bougies en Europe/Paris validees correctement."""
        idx = pd.date_range(
            "2025-01-15 09:00", periods=10, freq="1h",
            tz="Europe/Paris"
        )
        df = pd.DataFrame({"close": range(10)}, index=idx)

        audit = AuditDST(reference_date=date(2025, 1, 15))
        result = audit.check_candle_alignment(df, "Europe/Paris")
        assert result["pass"] is True
        assert result["tz_match"] is True
