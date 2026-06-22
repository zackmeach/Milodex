# tests/milodex/data/test_models.py
"""Tests for data layer models."""

import pandas as pd
import pytest

from milodex.data.models import Bar, BarSet


class TestBarSet:
    @pytest.fixture()
    def sample_df(self):
        return pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2025-01-13", "2025-01-14", "2025-01-15"], utc=True),
                "open": [148.0, 149.0, 150.0],
                "high": [149.0, 150.0, 152.0],
                "low": [147.0, 148.5, 149.5],
                "close": [148.5, 149.5, 151.0],
                "volume": [900000, 950000, 1000000],
                "vwap": [148.3, 149.2, 150.8],
            }
        )

    def test_to_dataframe_returns_copy(self, sample_df):
        barset = BarSet(sample_df)
        df = barset.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        df.iloc[0, df.columns.get_loc("close")] = 999.0
        assert barset.to_dataframe().iloc[0]["close"] != 999.0

    def test_latest_returns_bar(self, sample_df):
        barset = BarSet(sample_df)
        bar = barset.latest()
        assert isinstance(bar, Bar)
        assert bar.close == 151.0

    def test_latest_raises_on_empty(self):
        empty_df = pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"]
        )
        barset = BarSet(empty_df)
        with pytest.raises(ValueError, match="empty"):
            barset.latest()

    def test_validates_required_columns(self):
        bad_df = pd.DataFrame({"timestamp": [], "open": [], "close": []})
        with pytest.raises(ValueError, match="column"):
            BarSet(bad_df)

    def test_len(self, sample_df):
        barset = BarSet(sample_df)
        assert len(barset) == 3

    def test_vwap_nullable(self, sample_df):
        sample_df["vwap"] = [None, None, None]
        barset = BarSet(sample_df)
        assert len(barset) == 3

    def test_to_dataframe_exposes_seven_canonical_columns(self):
        """BarSet.to_dataframe() exposes all seven canonical columns.

        Columns: timestamp, open, high, low, close, volume, vwap (vwap auto-injected
        when omitted). The sidecar-metadata half (split state, dividends, delisted
        status, exchange/asset-type, calendar alignment) has no data model yet; out of
        scope here.
        """
        canonical = {"timestamp", "open", "high", "low", "close", "volume", "vwap"}
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2025-01-13"], utc=True),
                "open": [148.0],
                "high": [149.0],
                "low": [147.0],
                "close": [148.5],
                "volume": [900000],
                "vwap": [148.3],
            }
        )
        assert canonical <= set(BarSet(df).to_dataframe().columns)

        # vwap omitted -> BarSet auto-injects it as a nullable column.
        no_vwap = df.drop(columns=["vwap"])
        assert canonical <= set(BarSet(no_vwap).to_dataframe().columns)
