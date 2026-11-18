import argparse
import csv
from dataclasses import dataclass
from datetime import date
from math import sqrt
from pathlib import Path
from random import Random
from statistics import NormalDist, median

from figure import Plot

ROOT=Path(__file__).resolve().parent
ENTRY_DATE=date(2024,12,31)
MIN_CASH=25
FEE=25
ENTRY_MULT=10
CURE=60
SCENARIOS={"Base":(.06,.08,10),"Downside":(-.05,.10,8.25),"Severe":(-.20,.16,6.5)}


@dataclass(frozen=True)
class Financials:
    revenue:float
    operating_income:float
    depreciation:float
    amortization:float
    capex:float
    cash:float
    debt_current:float
    debt_long:float

    @property
    def ebitda(self):
        return self.operating_income+self.depreciation+self.amortization


def historical():
    with (ROOT/"historical.csv").open(newline="",encoding="utf-8") as csv_file:
        return Financials(**{r["field"]:float(r["value_usd_m"]) for r in csv.DictReader(csv_file)})


def sources_uses(financials, leverage=6):
    ev=ENTRY_MULT*financials.ebitda
    refinanced=financials.debt_current+financials.debt_long
    equity_price=ev+financials.cash-refinanced
    uses=equity_price+refinanced+FEE+MIN_CASH
    debt=leverage*financials.ebitda
    equity=uses-debt-financials.cash
    if equity<=0:
        raise ValueError("Sponsor entry equity must be positive")
    return dict(ev=ev,target_equity=equity_price,old_debt=refinanced,transaction_fees=FEE,
                initial_cash=MIN_CASH,total_uses=uses,acquired_cash=financials.cash,
                new_debt=debt,sponsor_equity=equity,total_sources=debt+equity+financials.cash)


def realized_irr(flows):
    flows=[(d,v) for d,v in flows if abs(v)>1e-10]
    signs=[v>0 for _,v in flows]
    changes=sum(a!=b for a,b in zip(signs,signs[1:]))
    if changes==0:
        return None,"no sign change"
    if changes!=1 or signs[0]:
        return None,"ambiguous sign pattern"
    t0=flows[0][0]
    def npv(rate):
        return sum(value/(1+rate)**((day-t0).days/365) for day,value in flows)
    lo,hi=-.999999,1
    while npv(hi)>0 and hi<1e7:
        hi=2*hi+1
    if npv(lo)<0 or npv(hi)>0:
        return None,"outside numeric bracket"
    for _ in range(80):
        mid=(lo+hi)/2
        if npv(mid)>0:
            lo=mid
        else:
            hi=mid
    return (lo+hi)/2,"unique"


def deal(fin,growth,rate,exit_multiple,years=5,repricing=0,leverage=6,cure_cap=CURE):
    entry=sources_uses(fin,leverage)
    original=entry["new_debt"]
    cash,debt=MIN_CASH,original
    prior_revenue=fin.revenue
    remaining=cure_cap
    flows=[(ENTRY_DATE,-entry["sponsor_equity"])]
    records=[]
    for year in range(1,years+1):
        end=date(ENTRY_DATE.year+year,12,31)
        change=(1+growth)**year
        ebitda,revenues=fin.ebitda*change,fin.revenue*change
        da=(fin.depreciation+fin.amortization)*change
        capex=fin.capex*change
        nwc=(revenues-prior_revenue)*.05
        day_rate=rate+(repricing if year>=2 else 0)
        interest=debt*day_rate
        tax=.25*max(0,ebitda-da-interest)
        cfads=ebitda-tax-capex-nwc
        amortization=min(.02*original,debt)
        before_principal=cash+cfads-interest
        headroom=before_principal-amortization-MIN_CASH
        cure=min(remaining,max(-headroom,0))
        remaining-=cure
        pre_amort=before_principal+cure
        unpaid_interest=max(0,-pre_amort)
        available=max(0,pre_amort)
        paid=min(amortization,available)
        sweep=min(debt-paid,.75*max(0,available-paid-MIN_CASH))
        cash_end=available-paid-sweep
        debt_end=debt+unpaid_interest-paid-sweep
        forced=headroom+cure < -1e-8
        multiple=exit_multiple*(.8 if forced else 1)
        terminal=forced or year==years
        ev=ebitda*multiple if terminal else 0
        exit_fee=.01*ev
        gross_equity=ev+cash_end-debt_end-exit_fee if terminal else 0
        exit_equity=max(0,gross_equity)
        flows.append((end,exit_equity-cure))
        covenant=(debt_end-cash_end)/ebitda>7
        records.append(dict(date=end.isoformat(),year=year,revenue=revenues,ebitda=ebitda,
            opening_cash=cash,opening_debt=debt,da=da,tax=tax,capex=capex,nwc_investment=nwc,cfads=cfads,
            interest_rate=day_rate,interest=interest,unpaid_interest=unpaid_interest,amortization_due=amortization,
            amortization_paid=paid,sweep=sweep,headroom=headroom,cure=cure,cash=cash_end,debt=debt_end,
            covenant_flag=covenant,forced_exit=forced,ev=ev,exit_fee=exit_fee,equity_floor=max(0,-gross_equity),
            exit_equity=exit_equity,sponsor_flow=exit_equity-cure))
        cash,debt,prior_revenue=cash_end,debt_end,revenues
        if terminal:
            break
    final=records[-1]
    irr,status=realized_irr(flows)
    topups=sum(r["cure"] for r in records)
    contributed=entry["sponsor_equity"]+topups
    changes=dict(operating=(ebitda-fin.ebitda)*ENTRY_MULT,
                 multiple=(multiple-ENTRY_MULT)*fin.ebitda,
                 interaction=(multiple-ENTRY_MULT)*(ebitda-fin.ebitda),
                 debt_paydown=original-debt,
                 cash_change=cash-MIN_CASH,
                 fees=-FEE-final["exit_fee"],topups=-topups,equity_floor=final["equity_floor"])
    return dict(entry=entry,rows=records,irr=irr,irr_status=status,moic=final["exit_equity"]/contributed,
                attributions=changes,net_sponsor=final["exit_equity"]-contributed,
                pre_cure_min=min(r["headroom"] for r in records),covenant=any(r["covenant_flag"] for r in records),
                forced=final["forced_exit"],exit_year=final["year"])


def grid(fin):
    return [dict(growth=g/100,rate=r/100,irr=case["irr"],headroom=case["pre_cure_min"],
                 forced=case["forced"],moic=case["moic"],irr_status=case["irr_status"])
            for g in range(-20,13,4) for r in range(6,17)
            for case in [deal(fin,g/100,r/100,8.25)]]


def joint(fin,count):
    rng=Random(1010)
    normal=NormalDist()
    rows=[]
    for simulation in range(count):
        g,eps1,eps2=(rng.gauss(0,1) for _ in range(3))
        growth=-.20+.32*normal.cdf(g)
        rate=.06+.10*normal.cdf(-.55*g+sqrt(1-.55**2)*eps1)
        multiple=6+6*normal.cdf(.65*g+sqrt(1-.65**2)*eps2)
        for exit_year in (3,5):
            for reset in (0,.02):
                d=deal(fin,growth,rate,multiple,years=exit_year,repricing=reset)
                rows.append(dict(simulation=simulation,planned_exit_year=exit_year,repricing=reset,
                     growth=growth,entry_rate=rate,multiple=multiple,irr=d["irr"],moic=d["moic"],
                     headroom=d["pre_cure_min"],covenant=d["covenant"],forced=d["forced"],
                     actual_exit_year=d["exit_year"],irr_status=d["irr_status"]))
    return rows


def write_csv(path,rows):
    with path.open("w",newline="",encoding="utf-8") as file:
        writer=csv.DictWriter(file,fieldnames=rows[0],lineterminator="\n")
        writer.writeheader()
        writer.writerows({k:round(v,8) if isinstance(v,float) else v for k,v in row.items()} for row in rows)


def charts(output,rows,changes):
    rates=sorted({r["rate"] for r in rows})
    growth=sorted({r["growth"] for r in rows})
    parts=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 920 522" role="img">',
           '<title>Sponsor IRR and liquidity boundary</title><rect width="920" height="522" fill="#fbfaf6"/>',
           '<g font-family="Arial,sans-serif" fill="#172f3d">',
           '<text x="25" y="38" font-size="25">Sponsor IRR and liquidity boundary</text>',
           '<text x="25" y="63" font-size="14">Hypothetical TaskUs buyout · fixed rate · planned year-five exit at 8.25×</text>',
           '<text x="504" y="470" text-anchor="middle" font-size="15">Annual debt coupon</text>',
           '<text x="25" y="502" font-size="13">Outlined cells breach the pre-cure cash floor · dash = no finite sponsor IRR</text>']
    for col,r in enumerate(rates):
        parts.append(f'<text x="{149+69*col}" y="104" text-anchor="middle" font-size="13">{r:.0%}</text>')
    for row,g in enumerate(reversed(growth)):
        y=114+37*row
        parts.append(f'<text x="104" y="{y+24}" font-size="14" text-anchor="end">{g:+.0%} growth</text>')
        for col,r in enumerate(rates):
            d=next(item for item in rows if item["rate"]==r and item["growth"]==g)
            irr=d["irr"]
            x=115+69*col
            color="#e7ded7" if irr is None else "#c0e1d8" if irr>=.15 else "#dde5d1" if irr>=0 else "#edd0bb"
            stroke="#922e41" if d["headroom"]<0 else "#fbfaf6"
            label="—" if irr is None else f"{irr:.0%}"
            parts.append(f'<rect x="{x}" y="{y}" width="65" height="33" rx="3" fill="{color}" stroke="{stroke}" stroke-width="2"/>')
            parts.append(f'<text x="{x+33}" y="{y+22}" text-anchor="middle" font-size="13">{label}</text>')
    (output/"liquidity.svg").write_text("\n".join(parts+["</g></svg>"])+"\n",encoding="utf-8")
    labels=("Start","Operating","Multiple","Interaction","Debt","Cash","Fees","Cures","Floor")
    points=[0.]
    for x in changes.values():
        points.append(points[-1]+x)
    low,high=min(points),max(points)
    p=Plot("Base sponsor value bridge","Hypothetical financing · cumulative contribution to net sponsor gain",
           (0,8),(low-40,high+40),"Contribution","USD millions",x_ticks=list(enumerate(labels)))
    p.series(list(enumerate(points)),"#176773","Net sponsor value")
    p.save(output/"bridge.svg")


def main():
    cli=argparse.ArgumentParser()
    cli.add_argument("--quick",action="store_true")
    cli.add_argument("--output",type=Path,default=ROOT/"report")
    args=cli.parse_args()
    args.output.mkdir(exist_ok=True,parents=True)
    fin=historical()
    deterministics={name:deal(fin,*spec) for name,spec in SCENARIOS.items()}
    results=grid(fin)
    stochastic=joint(fin,40 if args.quick else 800)
    write_csv(args.output/"sources-uses.csv",[deterministics["Base"]["entry"]])
    write_csv(args.output/"waterfall.csv",[dict(case=name,**row) for name,result in deterministics.items() for row in result["rows"]])
    write_csv(args.output/"attribution.csv",[dict(case=name,**r["attributions"],net_sponsor=r["net_sponsor"]) for name,r in deterministics.items()])
    write_csv(args.output/"sensitivity.csv",results)
    write_csv(args.output/"joint-stress.csv",stochastic)
    charts(args.output,results,deterministics["Base"]["attributions"])
    for name,r in deterministics.items():
        print(name,{k:v for k,v in r.items() if k not in ("rows","entry","attributions")})
    if not args.quick:
        for year in (3,5):
            for reset in (0,.02):
                a=[r for r in stochastic if r["planned_exit_year"]==year and r["repricing"]==reset]
                valid=[r["irr"] for r in a if r["irr"] is not None]
                print(year,reset,len(valid),median(valid),median(r["moic"] for r in a),
                      sum(r["forced"] for r in a),sum(r["headroom"]<0 for r in a))


if __name__=="__main__":
    main()
