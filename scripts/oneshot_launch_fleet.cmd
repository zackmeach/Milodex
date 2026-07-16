@echo off
rem One-shot fleet relaunch (scheduled-task target; keeps /tr under the 261-char cap).
cd /d C:\Users\zdm80\Milodex
.venv\Scripts\python.exe .claude\skills\fleet-ops\scripts\fleet.py deploy ^
  regime.daily.sma200_rotation.spy_shy.v1 ^
  breakout.daily.donchian_20_10.sector_etfs.v1 ^
  breakout.daily.atr_channel.sector_etfs.v1 ^
  momentum.daily.tsmom.curated_largecap.v1 ^
  meanrev.daily.pullback_rsi2.curated_largecap.v1 ^
  meanrev.daily.bbands_lowerband.curated_largecap.v1
