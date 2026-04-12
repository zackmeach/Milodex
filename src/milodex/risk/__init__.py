"""Risk management layer.

Sits between every strategy decision and every trade execution with veto
power. Enforces position sizing limits, daily loss caps, kill switch
thresholds, and fat-finger protections. The strategy proposes; risk
management disposes.
"""
