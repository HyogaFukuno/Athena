import csv,re,sys,statistics as st,glob
for t in sys.argv[1:]:
    d='run/sgbots-vps-out/'+t
    s=open(d+'/server.log',errors='replace').read()
    r=[(int(a),int(b)) for a,b in re.findall(r't=(\d+)s .*?worldGap=(\d+)ms',s)]
    g=sorted(x for _,x in r if x>0); g2=sorted(x for a,x in r if a>=60 and x>0)
    print('==',t,'gap med',st.median(g),'p95',g[int(len(g)*.95)],'max',g[-1],'| after60 med',st.median(g2),'p95',g2[int(len(g2)*.95)],'max',g2[-1],'>500:',sum(x>500 for x in g2),'/',len(g2),'>1000 at',[x for x in r if x[1]>1000][:6])
    try: rows=list(csv.DictReader(open(d+'/thread-stats.csv')))
    except Exception as e: print('no stats'); continue
    tot={k:sum(float(x[k]) for x in rows) for k in rows[0] if k.endswith('_us') and 'max' not in k}
    print(' windows',len(rows),'ticks',sum(int(x['ticks']) for x in rows))
    b=tot['busy_us']
    print(' busy %.1fs cpu %.1fs wait %.1f%%'%(b/1e6,tot['cpu_us']/1e6,100*(b-tot['cpu_us'])/b),{k[:-3]:round(100*tot[k]/b,1) for k in ['tasks_us','io_us','gen_us','preload_us','worlds_us','conn_us']})
    bk=[k for k in rows[0] if k.startswith('under_') or k=='longer']
    c={k:sum(int(x[k]) for x in rows) for k in bk}; n=sum(c.values()); acc=0; o={}
    for k in bk: acc+=c[k]; o[k.replace('under_','<')]=round(100*acc/n,2)
    print(' tick cumulative %',o)
    print(' maxes ms',{k[:-7]:round(max(float(x[k]) for x in rows)/1000) for k in rows[0] if k.endswith('_max_us')})
    print(' late sum s %.1f max tasks_queued %d'%(tot['late_us']/1e6,max(int(x['tasks_queued']) for x in rows)))
    # worst windows
    w=sorted(rows,key=lambda x:-float(x['max_busy_us']))[:4]
    t0=min(int(x['millis']) for x in rows)
    for x in w: print('  worst t+%ds %s busy_max %dms cpu/busy %.2f phases max'%((int(x['millis'])-t0)//1000,x['worlds'][:22],float(x['max_busy_us'])/1000,float(x['cpu_us'])/max(1,float(x['busy_us']))),{k[:-7]:round(float(x[k])/1000) for k in x if k.endswith('_max_us') and k not in('max_busy_us','max_late_us') and float(x[k])>20000})
    try:
        gc=open(d+'/gc.log',errors='replace').read()
        p=[float(x) for x in re.findall(r'Pause [^\n]*?(\d+\.\d+)ms',gc)]
        sp=[int(x)/1e6 for x in re.findall(r'Total: +(\d+) ns',gc)]
        print(' gc pauses',len(p),'sum %.0fms max %.1f'%(sum(p),max(p or [0])),'safepoints',len(sp),'sum %.0fms max %.1f'%(sum(sp),max(sp or [0])), 'concurrent lines',len(re.findall('Concurrent',gc)))
    except Exception as e: print(' no gc',e)
