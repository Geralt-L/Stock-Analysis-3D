#!/usr/bin/env python3
"""My own independent SOTP for Hesai (to reconcile against the 3-valuer panel). All RMB in millions unless noted."""
FX=6.78; ADS=156.44; PRICE=18.63
NET_CASH_USD = 7050.6/FX - 104   # ~936
def usd(rmb): return rmb/FX

# ---- Segment A: core lidar (ADAS + robotics lidar), valued on 2027E after-tax segment operating income (ex interest income)
# 2026E: lidar rev ~ 3.25M units x RMB1,320 = 4,290 (matches consensus group rev ~4,290 incl SGI 250 -> lidar ~4,040); take 4,050
# H1'26 lidar op income 108; H2 scale (1.9-2.4M units) -> FY26 lidar op income ~380 (9.4% margin)
# 2027E: consensus group rev 6,073; SGI ~700 -> lidar ~5,370; lidar op margin 12% -> 645; tax 15% -> 548 after-tax
lidar27_rev=5370; lidar27_opinc={'bear':5370*0.09,'base':5370*0.12,'bull':5370*0.14}
lidar27_nopat={k:v*0.85 for k,v in lidar27_opinc.items()}
mult_A={'bear':15,'base':22,'bull':28}
A={k: usd(lidar27_nopat[k])*mult_A[k] for k in mult_A}
# robotics lidar sub-line: 2027E ~950k units x RMB1,800 = 1,710 rev (~32% of lidar rev) with higher GM -> ~40% of segment value
# ---- Segment B: actuators / humanoid components (ex robotics lidar). 2027E rev: SGI ~700 total, actuators ~65% = 455
act27={'bear':300,'base':455,'bull':650}; ps_B={'bear':2.5,'base':5,'bull':9}
B={k: usd(act27[k]*ps_B[k]) for k in ps_B}
# ---- Segment C: Kosmo as option. scenario values (RMB m) & probabilities
scen={'fail_or_niche_hw':(0.50, 400), 'solid_b2b':(0.35, 4800), 'paradigm_winner':(0.15, 22000)}
kosmo_ev=sum(p*v for p,v in scen.values())
C={'bear':usd(300),'base':usd(kosmo_ev)*0.7,'bull':usd(15000)}   # base haircut 30% for execution/dilution of the tail
for k in ['bear','base','bull']:
    tot=A[k]+B[k]+C[k]+NET_CASH_USD
    print(f"{k:5s}: lidar {A[k]:6.0f} | actuators {B[k]:5.0f} | Kosmo {C[k]:5.0f} | cash {NET_CASH_USD:4.0f} | total US${tot:,.0f}M = ${tot/ADS:5.2f}/ADS ({(tot/ADS/PRICE-1)*100:+.0f}%)")
print(f"Kosmo prob-weighted EV RMB {kosmo_ev:,.0f}M = US${usd(kosmo_ev):,.0f}M (before haircut)")
# reverse read
mcap=PRICE*ADS; ev=mcap-NET_CASH_USD
print(f"\nEV US${ev:,.0f}M. If SGI+Kosmo were worth 0: EV/2027E lidar NOPAT(base) = {ev/usd(lidar27_nopat['base']):.1f}x ; EV/2026E lidar NOPAT(~380*0.85) = {ev/usd(380*0.85):.1f}x")
print(f"If core lidar deserves 22x 2027E NOPAT (US${usd(lidar27_nopat['base'])*22:,.0f}M), market implicitly pays US${ev-usd(lidar27_nopat['base'])*22:,.0f}M for actuators+Kosmo")
print(f"If core lidar deserves 15x: implied SGI value US${ev-usd(lidar27_nopat['base'])*15:,.0f}M")
