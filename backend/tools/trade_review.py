"""Offline trade report from a checksum-verified, frozen evidence bundle.

Standard library only: never imports the application, contacts a provider,
updates synchronization, registers a candidate or reads holdout outcomes.
"""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import html
import json
from pathlib import Path
import sqlite3
import subprocess
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
UTC = timezone.utc
METHOD = 'trade-review-v1'


def timestamp(value):
    if isinstance(value, (int,float)):
        return datetime.fromtimestamp(value,UTC)
    result = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if result.tzinfo is None:
        raise ValueError('Evidence timestamps must have an explicit timezone')
    return result.astimezone(UTC)


def session(value):
    return (timestamp(value).astimezone(ET)+timedelta(hours=6)).date().isoformat()


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def number(value):
    if value is None:
        return None
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError('Nonfinite financial value')
    return value


def money(value):
    return None if value is None else float(round(value,2))


def verify(bundle):
    manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    actual={p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    if actual != set(manifest['files'])|{'manifest.json'}:
        raise ValueError('Unmanifested or missing evidence member')
    for name, expected in manifest['files'].items():
        path=(bundle/name).resolve()
        if not path.is_relative_to(bundle.resolve()) or path.is_symlink():
            raise ValueError('Unsafe bundle member')
        if path.stat().st_size != expected['bytes'] or sha(path) != expected['sha256']:
            raise ValueError('Evidence integrity mismatch: '+name)
        if path.suffix == '.sqlite3':
            # immutable is safe ONLY for this closed, verified online snapshot.
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('SQLite integrity failure: '+name)
    return manifest


def readonly(bundle,name):
    return sqlite3.connect((bundle/name).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)


def costs(fill):
    raw=fill.get('raw_payload') or {}
    fees=number(fill.get('fees'))
    commissions=number(fill.get('commissions') if fill.get('commissions') is not None else raw.get('commissions'))
    # Provider fields are separate components, split per leg. Imported round-turn
    # rows already carry the whole round turn; never double either amount.
    return (fees or Decimal(0))+(commissions or Decimal(0)), fees is not None and commissions is not None


def attribution(attempts,owner):
    index=defaultdict(set)
    by_id={}
    for a in attempts:
        if a['user_id'] != owner or a['status'] != 'submitted':
            continue
        request=a.get('raw_request') or {}
        runner=(request.get('timeExit') or {}).get('runner') or {}
        by_id[a['id']]=a
        orders=[a.get('provider_order_id')]+[runner.get(k) for k in ('entry_order_id','exit_order_id','stop_order_id','target_order_id')]
        for order in orders:
            if order is not None:
                index[(int(a['account_id']),a['contract_id'],str(order))].add(a['id'])
    return index,by_id


def ledger(data,owner,cutoff,account_ids=None):
    accounts={int(a['external_id']):a for a in data['accounts'] if a['user_id']==owner and str(a['external_id']).isdigit()}
    if account_ids and not set(account_ids)<=set(accounts):
        raise ValueError('Selected account absent from owned evidence')
    selected=set(account_ids or accounts)
    index,attempts=attribution(data.get('bot_order_attempts',[]),owner)
    seen={}
    diagnostics=Counter()
    for f in data['projectx_trade_events']:
        if f['user_id'] != owner:
            diagnostics['other_owner_excluded']+=1
            continue
        if int(f['account_id']) not in selected or timestamp(f['trade_timestamp'])>cutoff:
            continue
        identity=f.get('source_trade_id')
        key=(owner,int(f['account_id']),str(identity) if identity is not None else 'local-row:'+str(f['id']))
        if key in seen and f != seen[key]:
            raise ValueError('Conflicting frozen fill versions; capture must resolve provenance explicitly')
        if key in seen:
            diagnostics['identical_duplicates']+=1
        seen[key]=f
    fills=[]
    grouped=defaultdict(list)
    for f in seen.values():
        if (f.get('raw_payload') or {}).get('voided') or str(f.get('status','')).upper()=='VOIDED':
            diagnostics['voided_legs']+=1
            continue
        if f['side'] not in ('BUY','SELL') or (number(f['size']) or 0)<=0:
            diagnostics['invalid_legs']+=1
            continue
        f=dict(f)
        fee,complete=costs(f)
        f.update(known_costs=money(fee),fee_complete=complete)
        fills.append(f)
        grouped[(int(f['account_id']),f['contract_id'])].append(f)
    positions=[]
    residual=[]
    for (account,contract),rows in sorted(grouped.items()):
        # Closing legs precede reversal opening legs at an identical timestamp.
        rows.sort(key=lambda r:(timestamp(r['trade_timestamp']),r['pnl'] is None,str(r.get('source_trade_id') or r['id'])))
        quantity=Decimal(0)
        current=None
        for f in rows:
            size=number(f['size']); price=number(f['price'])
            signed=size if f['side']=='BUY' else -size
            pnl=number(f['pnl']); fee,fee_complete=costs(f)
            imported=f.get('fee_scope')=='round_turn' and f.get('entry_timestamp') is not None
            if imported:
                current_import=dict(account_id=account,contract=contract,open_utc=f['entry_timestamp'],close_utc=f['trade_timestamp'],
                    side='LONG' if f['side']=='SELL' else 'SHORT',qty_max=money(size),fill_ids=[str(f.get('source_trade_id') or f['id'])],
                    orders=[str(f['order_id'])],gross=money(pnl),known_costs=money(fee),fee_complete=fee_complete,
                    net=money(pnl-fee) if pnl is not None and fee_complete else None,flags=[],source='reviewed_import')
                positions.append(current_import)
                continue
            if quantity == 0 and pnl is not None:
                diagnostics['orphan_closing_legs']+=1
                continue  # Never turn a missing entry into a fabricated opening.
            if current is None:
                current=dict(account_id=account,contract=contract,side='LONG' if signed>0 else 'SHORT',
                    open_utc=f['trade_timestamp'],close_utc=None,qty_max=Decimal(0),fill_ids=[],orders=set(),
                    gross=Decimal(0),known_costs=Decimal(0),fee_complete=True,flags=[],source='provider_legs')
            closing=quantity*signed<0
            if closing and abs(signed)>abs(quantity):
                current['flags'].append('closing_quantity_exceeds_known_entry')
                # ProjectX publishes split reversal legs, not an aggregate fill.
                # Retain this uncertainty; no synthetic allocation of broker P&L.
            if not closing and pnl is not None:
                current['flags'].append('closing_leg_without_matching_direction')
            if closing and pnl is None:
                current['flags'].append('closing_pnl_missing')
            quantity+=signed
            current['qty_max']=max(current['qty_max'],abs(quantity))
            current['fill_ids'].append(str(f.get('source_trade_id') or f['id']))
            current['orders'].add(str(f['order_id']))
            current['gross']+=pnl or 0
            current['known_costs']+=fee
            current['fee_complete'] &= fee_complete
            if quantity == 0:
                current['close_utc']=f['trade_timestamp']
                current['net']=money(current['gross']-current['known_costs']) if current['fee_complete'] and not current['flags'] else None
                for k in ('gross','known_costs','qty_max'): current[k]=money(current[k])
                if current['flags']: current['gross']=None
                current['orders']=sorted(current['orders'])
                positions.append(current)
                current=None
        if current:
            residual.append(dict(account_id=account,contract=contract,quantity=float(quantity),
                                 opened=current['open_utc'],state='unreconciled_fill_inventory',
                                 note='May be missing fills; not a claim of current broker exposure'))
    for p in positions:
        matched=set().union(*(index.get((p['account_id'],p['contract'],order),set()) for order in p['orders']))
        p['bot_entry_ids']=sorted(matched)
        p['attribution']='bot' if len(matched)==1 else 'ambiguous_bot_match' if matched else 'not_bot'
        versions={(attempts[i].get('raw_request') or {}).get('strategySnapshot',{}).get('strategy_version_id') for i in matched}
        p['strategy_version']=next(iter(versions)) if len(versions)==1 else None
        modes={(attempts[i].get('raw_request') or {}).get('strategySnapshot',{}).get('rules',{}).get('exit',{}).get('exit_mode') or
                ((attempts[i].get('raw_request') or {}).get('timeExit') or {}).get('runner',{}).get('exit_mode') for i in matched}
        p['strategy_class']=next(iter(modes)) if len(modes)==1 else None
        p['session']=session(p['open_utc'])
        p['open_et']=timestamp(p['open_utc']).astimezone(ET).isoformat()
        p['close_et']=timestamp(p['close_utc']).astimezone(ET).isoformat()
        p['hold_seconds']=(timestamp(p['close_utc'])-timestamp(p['open_utc'])).total_seconds()
        a=accounts[p['account_id']]
        p['environment']='broker_simulated' if a.get('provider_simulated') is True else 'broker_live' if a.get('provider_simulated') is False else 'unknown'
        p['environment_basis']='account classification at capture; historical classification not independently verified'
        p['position_id']=sha256_text(json.dumps([owner,p['account_id'],p['contract'],p['fill_ids']],sort_keys=True))[:20]
        p['journal_ids']=[j['id'] for j in data.get('journal_entries',[]) if j['user_id']==owner and
            j.get('account_id')==a.get('id') and str(j.get('entry_date'))==p['session']]
        p['journal_join_basis']='owned local account row + session date; journal narrative does not prove manual attribution'
    positions.sort(key=lambda r:(timestamp(r['close_utc']),r['account_id'],r['position_id']))
    return positions,fills,residual,dict(diagnostics),accounts


def sha256_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def paths(bundle,owner,positions):
    if not (bundle/'progress.sqlite3').exists():
        for p in positions: p['path']={'state':'no_store'}
        return []
    with closing(readonly(bundle,'progress.sqlite3')) as db:
        db.row_factory=sqlite3.Row
        scopes=[dict(r) for r in db.execute('SELECT * FROM trade_progress_scopes WHERE owner=?',(owner,))]
        enrolled={r['account']:r for r in scopes}
        for p in positions:
            start,end=timestamp(p['open_utc']).timestamp(),timestamp(p['close_utc']).timestamp()
            candidates=db.execute('SELECT id,state FROM trade_progress_positions WHERE owner=? AND account=? AND contract=? '
                'AND opened<=? AND (ended IS NULL OR ended>=?)',(owner,p['account_id'],p['contract'],end,start)).fetchall()
            state='not_enrolled' if p['account_id'] not in enrolled else 'no_matching_recording'
            p['path']={'state':state,'candidate_ids':[r['id'] for r in candidates]}
            if len(candidates)!=1:
                if candidates: p['path']['state']='ambiguous_or_split_recordings'
                continue
            record=json.loads(candidates[0]['state'])
            observed=[json.loads(r[0]) for r in db.execute('SELECT payload FROM trade_progress_samples WHERE position=? AND ts>=? AND ts<=? ORDER BY ts,id',
                (candidates[0]['id'],start,end))]
            usable=[r for r in observed if not r.get('issue') and r.get('points') is not None and
                    r.get('mark_timestamp') is not None and start<=r['mark_timestamp']<=end]
            p['path']=dict(state='partially_recorded',candidate_ids=[record['id']],samples=len(observed),valid_marks=len(usable),
                first_mark_delay_seconds=usable[0]['ts']-start if usable else None,
                observed_best_points=max((r['points'] for r in usable),default=None),
                observed_worst_points=min((r['points'] for r in usable),default=None),
                flags=record.get('reasons',[]),basis='One-second valuation marks within broker entry/exit; not executable prices; raw post-exit samples preserved')
    return scopes


def study(bundle,positions,cutoff):
    if not (bundle/'exit-results.sqlite3').exists():
        return {'state':'not_examined','groups':[]}
    actual={i:p for p in positions for i in p['bot_entry_ids'] if p['attribution']=='bot'}
    selected={}
    with closing(readonly(bundle,'exit-results.sqlite3')) as db:
        # Filter phase in SQL before deserializing any outcome. Never inspect
        # candidate selections or the held-out payloads.
        rows=db.execute("SELECT r.account,r.entry,r.experiment,r.payload FROM results r JOIN enrollments e "
            "ON e.account=r.account AND e.entry=r.entry AND e.experiment=r.experiment WHERE e.phase='exploratory' ORDER BY r.seq")
        for account,entry,experiment,payload in rows:
            r=json.loads(payload)
            if r.get('phase')!='exploratory' or timestamp(r['entry_at'])>cutoff:
                continue
            selected[(account,entry,experiment)]=r
    groups={}; reasons=Counter(); detail=[]
    for (account,entry,experiment),r in selected.items():
        if entry not in actual: continue
        p=actual[entry]; pnl=p['net']
        outcome='unknown' if pnl is None else 'winner' if pnl>0 else 'loser' if pnl<0 else 'flat'
        key=(account,experiment,r.get('session'),r.get('strategy_version_id'),outcome)
        g=groups.setdefault(key,dict(account=account,experiment=experiment,session=r.get('session'),version=r.get('strategy_version_id'),strategy_class=p.get('strategy_class'),outcome=outcome,entries=0,paired_valid=0))
        g['entries']+=1; g['paired_valid']+=bool(r['paired_valid'])
        missing=Counter(v.get('reason','unresolved') for v in r['variants'] if v['status']!='resolved')
        reasons.update(missing)
        detail.append(dict(account=account,entry=entry,experiment=experiment,paired_valid=r['paired_valid'],reasons=dict(missing),capture_id=r.get('capture_id'),replay=r.get('replay')))
    return dict(state='exploratory_only',groups=[dict(g,rate=g['paired_valid']/g['entries']) for g in groups.values()],
                missing_variant_reasons=dict(reasons),entries=detail,
                unmatched_results=len(selected)-len(detail),
                warning='All alternatives must resolve. Winners need longer tails than shared-stop losers. Exclusions can bias every comparison; no prospective validation or candidate choice performed.')


def csv_file(path,rows,fields):
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for r in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list,set)) else v for k,v in r.items() if k in fields})


def totals(rows):
    known=[p['net'] for p in rows if p['net'] is not None]
    return dict(positions=len(rows),net_known_positions=len(known),net_sum_known=round(sum(known),2),
                gross_sum_known=round(sum(p['gross'] for p in rows if p['gross'] is not None),2),
                known_costs=round(sum(p['known_costs'] for p in rows),2),fee_incomplete=sum(not p['fee_complete'] for p in rows),
                winners=sum(v>0 for v in known),losers=sum(v<0 for v in known),sessions=len({p['session'] for p in rows}))


def history_coverage(bundle, owner, selected, start, end):
    windows=[]
    reconciliation={}
    if (bundle/'history-sync.sqlite3').exists():
        with closing(readonly(bundle,'history-sync.sqlite3')) as db:
            db.row_factory=sqlite3.Row
            windows.extend(dict(r) for r in db.execute('SELECT * FROM windows WHERE owner=?',(owner,)) if r['account'] in selected)
    if (bundle/'provider-coverage.json').exists():
        collected=json.loads((bundle/'provider-coverage.json').read_text(encoding='utf-8'))
        if collected['request']['owner']!=owner: raise ValueError('Provider coverage owner mismatch')
        windows.extend(r for r in collected['windows'] if r['account'] in selected)
        reconciliation={k:v for k,v in collected.items() if k!='windows'}
    states={}
    for account in selected:
        rows=[r for r in windows if r['account']==account]
        intervals=sorted((timestamp(r.get('start') or r['fetched_start']), timestamp(r.get('end') or r['fetched_end']))
                         for r in rows if r['state'] in ('response_covered','provisional'))
        cursor=start
        for left,right in intervals:
            if left<=cursor: cursor=max(cursor,right)
        states[account]='response_covered' if cursor>=end else 'partial' if intervals else (
            'temporarily_inaccessible' if any(r['state']=='temporarily_inaccessible' for r in rows) else
            'unavailable' if rows else 'not_examined')
    return windows,states,reconciliation


def cashflows(data, owner, selected, start_day, end_day, restricted_accounts):
    result={}
    for table,date_field in [('expenses','expense_date'),('payouts','payout_date')]:
        rows=[r for r in data.get(table,[]) if r['user_id']==owner and start_day<=r[date_field]<=end_day]
        scoped=[r for r in rows if not restricted_accounts or r.get('account_id') in selected]
        amounts=defaultdict(int)
        for r in scoped: amounts[r['currency']]+=int(r['amount_cents'])
        result[table]={'rows':len(scoped),'amounts_by_currency':{k:v/100 for k,v in amounts.items()},
                       'unallocated_rows_excluded':len(rows)-len(scoped)}
    return dict(result,period=[start_day,end_day],basis='Recorded calendar dates; cash flows kept separate from trading P&L. Payouts without an account are excluded for account-filtered reports. Deposits, resets and adjustments are not independently classified.')


def source_overlaps(accounts, selected):
    names=defaultdict(list)
    for account in sorted(selected):
        row=accounts[account]
        if row.get('name'):names[row['name'].strip().casefold()].append(account)
    return [dict(accounts=ids,state='unreviewed_source_overlap',basis='Same account name across CSV and provider sources; not proof of identity. No rows merged.')
            for ids in names.values() if len(ids)>1 and
            {'csv_import','projectx'}<={accounts[i].get('trade_data_source') for i in ids}]


def table(rows,fields):
    def cell(v): return html.escape(json.dumps(v) if isinstance(v,(dict,list)) else str(v) if v is not None else 'unavailable')
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+cell(k.replace('_',' ').capitalize())+'</th>' for k in fields)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+cell(r.get(k))+'</td>' for k in fields)+'</tr>' for r in rows)+'</tbody></table></div>'


def chart(positions):
    groups=defaultdict(float)
    for p in positions:
        if p['net'] is not None: groups[p['session']]+=p['net']
    values=list(sorted(groups.items()))
    if not values: return '<p>No fee-complete closed-position P&amp;L available.</p>'
    bound=max(1,max(abs(v) for _,v in values)); width=max(2,880/len(values))
    bars=[]
    for i,(day,value) in enumerate(values):
        height=110*abs(value)/bound
        bars.append(f'<rect x="{40+i*880/len(values):.2f}" y="{140-height if value>=0 else 140:.2f}" width="{width:.2f}" height="{height:.2f}" fill="{"#58c9a3" if value>=0 else "#f09088"}"><title>{day}: ${value:,.2f}</title></rect>')
    return '<svg viewBox="0 0 960 290" role="img" aria-label="Session net P and L for positions with known costs"><line x1="40" y1="140" x2="920" y2="140" stroke="#8494aa"/>'+''.join(bars)+f'<text x="40" y="20" fill="#ccd6e3">Scale: ±${bound:,.0f} · known-cost subset</text><text x="40" y="280" fill="#ccd6e3">{values[0][0]} to {values[-1][0]} · session net; hover for values</text></svg>'


def generate(args):
    bundle=args.bundle.resolve(); manifest=verify(bundle)
    if manifest['owner']!=args.owner: raise ValueError('Bundle owner mismatch')
    cutoff=timestamp(args.cutoff)
    if cutoff>timestamp(manifest['capture_completed_at']):
        raise ValueError('Cutoff exceeds captured evidence; capture again or use an earlier cutoff')
    data=json.load(gzip.open(bundle/'execution.json.gz','rt',encoding='utf-8'))
    positions,fills,residual,diagnostics,accounts=ledger(data,args.owner,cutoff,args.accounts)
    if args.start_session: positions=[p for p in positions if p['session']>=args.start_session]
    if args.end_session: positions=[p for p in positions if p['session']<=args.end_session]
    if args.cohort!='all':
        positions=[p for p in positions if p['attribution']==args.cohort.replace('-','_')]
    if args.strategy_version: positions=[p for p in positions if p['strategy_version']==args.strategy_version]
    scopes=paths(bundle,args.owner,positions)
    comparison=study(bundle,positions,cutoff)
    selected=set(args.accounts or accounts)
    earliest=min((timestamp(f['trade_timestamp']) for f in fills),default=cutoff)
    first_session=args.start_session or session(earliest)
    requested_start=(datetime.fromisoformat(first_session+'T18:00:00').replace(tzinfo=ET)-timedelta(days=1)).astimezone(UTC)
    requested_end=min(cutoff,datetime.fromisoformat(args.end_session+'T17:00:00').replace(tzinfo=ET).astimezone(UTC)) if args.end_session else cutoff
    covered,history_states,reconciliation=history_coverage(bundle,args.owner,selected,requested_start,requested_end)
    overlaps=source_overlaps(accounts,selected)
    coverage=dict(status='materially_incomplete',cutoff=cutoff.isoformat(),capture_started_at=manifest['capture_started_at'],
        capture_completed_at=manifest['capture_completed_at'],diagnostics=diagnostics,history_windows=covered,
        provider_reconciliation=reconciliation,source_overlap_candidates=overlaps,requested_range=[requested_start.isoformat(),requested_end.isoformat()],
        accounts=[dict(account=a,history=history_states[a],
                       fill_rows=sum(int(f['account_id'])==a for f in fills),
                       recording='enrolled_at_capture' if any(r['account']==a and r['enabled'] for r in scopes) else 'not_enrolled') for a in sorted(selected)],
        recorder_at_capture=scopes,path_states=dict(Counter(p['path']['state'] for p in positions)),
        unresolved_fill_inventory=residual,study=comparison,
        notes=['Provider responses do not prove unlimited historical availability.',
               'No balance movement is converted into trades, profit, or a daily-limit trigger.',
               'Not-bot attribution is not confirmed manual action. Timing similarity is not a proven copy decision.',
               'Legacy owners are never merged without a separately reviewed ownership mapping.',
               'A completed position is a reconstructed flat-to-flat inventory cycle. Provider legs, executions, decisions and copy groups are different units.'])
    local_cutoff=cutoff.astimezone(ET)
    last_session=args.end_session or session(cutoff)
    close=datetime.fromisoformat(last_session+'T17:00:00').replace(tzinfo=ET)
    coverage['session_state']='provisional' if local_cutoff<close else 'closed_by_requested_cutoff'
    coverage['session_boundary']='18:00 America/New_York; report date labels next session. Exchange holiday completeness is not inferred.'
    gaps=[]
    if not args.start_session: gaps.append('all_history_has_no_verified_inception_or_provider_retention_guarantee')
    if any(v!='response_covered' for v in history_states.values()): gaps.append('history_response_ranges_incomplete')
    if residual or diagnostics.get('orphan_closing_legs'): gaps.append('unreconciled_fill_inventory')
    if any(not p['fee_complete'] or p['flags'] for p in positions): gaps.append('financial_coverage_incomplete')
    if any(p['path']['state']!='fully_recorded' for p in positions): gaps.append('path_coverage_incomplete')
    if overlaps:gaps.append('unreviewed_cross_source_account_overlap')
    coverage['material_gaps']=gaps
    coverage['status']='materially_incomplete' if gaps else 'provisional' if coverage['session_state']=='provisional' else 'complete_within_requested_scope'
    summary=totals(positions)
    summary['aggregate_net_pnl']=None if overlaps or residual or summary['fee_incomplete'] else summary['net_sum_known']
    summary['aggregate_note']='Combined profit withheld for unresolved source overlap, fill inventory or costs. Known net is a sum of available scoped rows and may double-count overlapping source accounts.' if summary['aggregate_net_pnl'] is None else 'Reconciled scoped closed-position net only; not current account equity.'
    summary['cashflows']=cashflows(data,args.owner,selected,first_session,args.end_session or session(cutoff),bool(args.accounts))
    summary['by_environment']={k:totals([p for p in positions if p['environment']==k]) for k in sorted({p['environment'] for p in positions})}
    summary['by_attribution']={k:totals([p for p in positions if p['attribution']==k]) for k in sorted({p['attribution'] for p in positions})}
    # Reconcile on the fill ledger separately from closed lifecycle metrics.
    summary['authoritative_fill_ledger']=dict(rows=len(fills),gross_reported=money(sum((number(f['pnl']) or 0 for f in fills),Decimal(0))),
        known_costs=round(sum(f['known_costs'] for f in fills),2),fee_incomplete=sum(not f['fee_complete'] for f in fills),
        scope='Selected accounts through cutoff, including pre-range entry context; not restricted by position cohort')
    output=args.output.resolve()
    if output.exists(): raise FileExistsError('Use a new output directory; previous reports are immutable')
    output.mkdir(parents=True)
    def write(name,obj): (output/name).write_text(json.dumps(obj,indent=2,ensure_ascii=False,default=str),encoding='utf-8')
    write('coverage.json',coverage);write('summary.json',summary);write('positions.json',positions)
    csv_file(output/'trades.csv',positions,['position_id','account_id','contract','side','qty_max','open_et','close_et','session','hold_seconds','gross','known_costs','fee_complete','net','attribution','bot_entry_ids','strategy_version','environment','fill_ids','flags','path'])
    csv_file(output/'fills.csv',fills,['id','user_id','account_id','source_trade_id','order_id','contract_id','side','size','price','trade_timestamp','pnl','known_costs','fee_complete','raw_payload'])
    prose=(f'# Trade review through {local_cutoff.isoformat()}\n\n'
        f'**{coverage["status"].replace("_"," ").capitalize()}; session state: {coverage["session_state"]}.** '
        f'{summary["positions"]} reconstructed closed positions across {summary["sessions"]} sessions. '
        f'Known net P&L is ${summary["net_sum_known"]:,.2f} on {summary["net_known_positions"]} positions; '
        f'{summary["fee_incomplete"]} positions have incomplete costs. Environments are separated in summary.json.\n\n'
        f'{summary["aggregate_note"]}\n\n'
        'History coverage is not established for every requested account/range. Missing paths and uninitialized excursions are unavailable, not zero. '
        'Open fill inventory is unreconciled and may reflect missing history. No strategy, order, candidate or holdout was changed.\n\n'
        'Use coverage.json for account/range states, recorder health at capture, path exclusions and exploratory study inclusion by actual outcome, session and version. '
        'The original November 12 report substituted bot-journal outcomes and merged legacy owners; this report uses only scoped broker legs and reviewed imports. '
        'Do not compare headline totals without reconciling those scope and cost differences.\n\n'
        'Artifacts: report.html, trades.csv, fills.csv, positions.json, summary.json, coverage.json and provenance.json.\n')
    (output/'summary.md').write_text(prose,encoding='utf-8')
    methods=('Provider legs are authoritative for reported gross P&L. Fees and commissions are counted once per leg; missing components withhold net P&L. '
        'Observed midpoint/last-price paths are valuations, not executable fills; broker entry and exit times bound path metrics. '
        'Historical candle estimates and exit-study counterfactuals are separate from actual results. Copy similarity is not a proven decision; aggregate exposure across accounts is not per-account risk. '
        'Payouts, deposits, resets and expenses are cash flows, excluded from trading P&L. Balances cannot identify their cause. '
        'Holding-time, direction, time-of-day and post-loss patterns are retrospective associations. Session clustering, small samples, multiple comparisons, repeated peeking and selective exclusions limit inference. '
        'A proposed stop at 40% of a daily loss limit is an unvalidated hypothesis and cannot guarantee a loss cap. '
        'For gross win W, gross loss L and round-turn cost C, break-even win probability is (L+C)/(W+L). At W=L=$50 and C=$1.72 it is 51.72%; slippage, variable costs and dependent trades change that assumption. '
        'No claim about stops being deliberately targeted can be established by these observations. Prospective validation must follow the existing registered protocol.')
    fields=['account','experiment','session','version','strategy_class','outcome','entries','paired_valid','rate']
    page='<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TopSignal trade review</title><style>body{background:#0d1624;color:#e7edf5;font:16px system-ui;margin:0}main{max-width:1180px;margin:auto;padding:40px 24px}h1{font-size:38px}h2{margin-top:40px}p{line-height:1.65;color:#c4cfdd}a{color:#79cafa}.notice{padding:20px;background:#473326;border-left:4px solid #f4b578}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:10px;border-bottom:1px solid #2b3b50;text-align:left;vertical-align:top}svg{width:100%;max-height:320px}code{overflow-wrap:anywhere}.cards{display:flex;gap:20px;flex-wrap:wrap}.card{background:#192638;padding:20px;border-radius:10px}.num{font-size:28px}details{margin:20px 0}</style><main>'
    page+=f'<p>TOPSIGNAL · FROZEN EVIDENCE REVIEW</p><h1>Trade performance and evidence coverage</h1><p>Through {html.escape(local_cutoff.isoformat())} · America/New_York</p><div class="notice"><strong>{coverage["status"].replace("_"," ").capitalize()}</strong><p>Session state: {coverage["session_state"].replace("_"," ")}. Results are conditional on available fills and known costs. See coverage.json for exact response ranges and remaining gaps.</p></div>'
    page+='<p>'+html.escape(summary['aggregate_note'])+'</p>'
    page+=f'<div class="cards"><div class="card">Closed positions<div class="num">{len(positions):,}</div></div><div class="card">Known net P&amp;L<div class="num">${summary["net_sum_known"]:,.2f}</div>{summary["net_known_positions"]} fee-complete positions</div><div class="card">Observed sessions<div class="num">{summary["sessions"]}</div></div></div>'
    page+='<h2>Actual closed-position results</h2>'+chart(positions)+table([dict(environment=k,**v) for k,v in summary['by_environment'].items()],['environment','positions','net_known_positions','net_sum_known','known_costs','fee_incomplete'])
    displayed_scopes=[dict(r,heartbeat=timestamp(r['heartbeat']).astimezone(ET).isoformat() if r.get('heartbeat') else None,error=r.get('error') or 'None reported') for r in scopes]
    page+='<h2>Recorder and history coverage</h2><p>Enrollment and heartbeat describe the capture time, not all historical sessions. Raw observations after exits remain preserved.</p>'+table(displayed_scopes,['account','enabled','heartbeat','error'])+table([{'path_state':k.replace('_',' '),'positions':v} for k,v in coverage['path_states'].items()],['path_state','positions'])
    page+='<h2>Exit-study inclusion</h2><p>'+html.escape(comparison.get('warning','Study not examined'))+'</p>'+table(comparison['groups'],fields)
    page+='<h2>Cash flows in the same reporting period</h2><p>'+html.escape(summary['cashflows']['basis'])+'</p>'+table([dict(kind=k,**summary['cashflows'][k]) for k in ('expenses','payouts')],['kind','rows','amounts_by_currency','unallocated_rows_excluded'])
    page+='<h2>Method and limits</h2><p>'+html.escape(methods)+'</p><h2>Evidence and reconciliation</h2><p><a href="trades.csv">Trade-level CSV</a> · <a href="fills.csv">Provider leg CSV</a> · <a href="coverage.json">Coverage and reconciliation</a> · <a href="summary.md">Markdown summary</a> · <a href="provenance.json">Provenance and hashes</a></p><details><summary>All account coverage states</summary>'+table(coverage['accounts'],['account','history','fill_rows','recording'])+'</details><details><summary>Closed positions</summary>'+table(positions,['position_id','account_id','open_et','close_et','contract','gross','known_costs','net','attribution','flags'])+'</details></main></html>'
    (output/'report.html').write_text(page,encoding='utf-8')
    try: commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=Path(__file__).resolve().parents[2],text=True).strip()
    except (OSError,subprocess.CalledProcessError): commit='unavailable'
    provenance=dict(method=METHOD,cutoff=cutoff.isoformat(),generated_at=datetime.now(UTC).isoformat(),code_commit=commit,
        generator_sha256=sha(Path(__file__)),bundle=str(bundle),bundle_manifest_sha256=sha(bundle/'manifest.json'),
        capture_manifest=manifest,parameters={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
        assumptions=methods,exclusions=diagnostics,files={p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in output.iterdir() if p.is_file()})
    write('provenance.json',provenance)
    print(json.dumps({'output':str(output),'status':coverage['status'],'summary':summary}))
    return coverage,summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',required=True,type=Path)
    p.add_argument('--owner',required=True)
    p.add_argument('--cutoff',required=True,help='ISO-8601 with UTC offset')
    p.add_argument('--start-session');p.add_argument('--end-session')
    p.add_argument('--accounts',type=int,nargs='+')
    p.add_argument('--cohort',choices=['all','bot','not-bot','manual'],default='all')
    p.add_argument('--strategy-version')
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    if args.cohort=='manual': p.error('Confirmed manual attribution is unavailable in this evidence schema; use not-bot and retain that label')
    generate(args)


if __name__=='__main__': main()
