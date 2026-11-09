# 試合の始まりの tick の山 (thread-stats.csv) と、同じ 5 秒の経路探索・セクションの読み込み (-Dsgbots.gridstats の行) を部屋ごとに並べる
# python3 startspike.py results/u-fix-1 results/u-fix-2 ...
import csv,sys,re,collections,os
rowsout=[]
for d in sys.argv[1:]:
    ts=collections.defaultdict(list)
    for r in csv.DictReader(open(d+'/thread-stats.csv')):
        w=r['worlds'].split()[0] if r['worlds'] else ''
        if 'Shady' in w: ts[w].append((int(r['millis']),int(r['max_busy_us'])/1000,int(r['busy_us'])/1000,int(r['ticks'])))
    g=collections.defaultdict(list)
    for line in open(d+'/server.log' if os.path.exists(d+'/server.log') else d+'/gridstats.log'):
        m=re.search(r'\[GridStats\] (\d+) (\S+) (.*)',line)
        if m: g[m.group(2)].append((int(m.group(1)),{k:float(v) for k,v in (x.split('=') for x in m.group(3).split())}))
    for w in sorted(ts):
        if w not in g: continue
        t0=ts[w][0][0]
        first=[x for x in ts[w] if x[0]-t0<=10000]
        bmax=max(first,key=lambda x:x[1])
        gs=[kv for t,kv in g[w] if bmax[0]-5000<=t<=bmax[0]+1000]
        rd=max([kv['max_window_ms'] for kv in gs] or [0]); pl=max([kv['max_window_plan_ms'] for kv in gs] or [0]); pt=sum(kv['plan_ms'] for kv in gs); fr=sum(kv['first'] for kv in gs)
        later=max([x[1] for x in ts[w] if x[0]-t0>30000] or [0])
        rowsout.append((d.split('/')[-1],w,bmax[1],round((bmax[0]-t0)/1000),rd,pl,later,pt,fr))
for r in rowsout: print('%s %-20s start_max=%4.0f@%2ss  grid_read_win=%4.1f plan_win=%4.1f  later_max=%4.0f plan_sum=%4.0f first=%3.0f'%r)
import statistics
sm=[r[2] for r in rowsout]; rd=[r[4] for r in rowsout]; pl=[r[5] for r in rowsout]; lt=[r[6] for r in rowsout]
print('n',len(rowsout),'start_max med',statistics.median(sm),'max',max(sm),'| read_win med',statistics.median(rd),'max',max(rd),'| plan_win med',statistics.median(pl),'max',max(pl),'| later med',statistics.median(lt))
