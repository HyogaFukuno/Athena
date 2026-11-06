import json,collections,re,sys
D=sys.argv[1]
def fr(f):
    m=f['method']; return m['type']['name'].split('/')[-1]+'.'+m['name']
ev=json.load(open(D+'/exec.json'))['recording']['events']
incl=collections.Counter(); N=len(ev); seg=collections.Counter(); segn=0; pat=r'(AbstractMemorySegment|SegmentVarHandle|VarHandle|MemorySession|Unsafe|ScopedMemory|NativeMemory|HeapMemory|MemorySegment|Invokers|LambdaForm|DirectMethod|Objects|ValueLayouts|Preconditions)'
for e in ev:
    names=[fr(f) for f in ((e['values'].get('stackTrace') or {}).get('frames') or [])]
    for x in set(names): incl[x]+=1
    if names and re.match(pat,names[0]):
        segn+=1
        for n in names:
            if not re.match(pat,n): seg[n]+=1; break
print('samples',N,'memory segment self %',round(100*segn/N,1),[(k,round(100*c/N,1)) for k,c in seg.most_common(8)])
sel=lambda pats:sorted([(k,round(100*c/N,1)) for k,c in incl.items() if any(re.search(p,k) for p in pats)],key=lambda x:-x[1])
print('rand/lamp',sel([r'^Random\.next',r'nextGaussian',r'^AtomicLong\.',r'RedstoneLamp']))
print('tick',sel([r'^AthenaWorldThread\.run',r'athenaTickWorld0?$',r'tickConnections',r'^WorldServer\.doTick',r'^World\.tickEntities',r'^WorldServer\.tickEntities',r'^EntityTracker\.updatePlayers',r'^PlayerChunkMap\.flush',r'^CraftScheduler\.mainThreadHeartbeat',r'^AthenaWorldTimer',r'^WorldServer\.athenaRun']))
print('bots',sel([r'^SgBot\.tick',r'^Navigator\.plan',r'^Terrain\.standable',r'^AthenaBotPlayer\.(onUpdate|move)',r'hasLineOfSight',r'CraftWorld\.getBlockTypeIdAt',r'^Scoreboards\.render$',r'^Entity\.move',r'^World\.getCubes',r'^Chunk\.getBlockData',r'^ChunkSection\.(getType|recalcBlockCounts)',r'^Block\.getId',r'^CraftMagicNumbers\.getId',r'^RegistryID\.',r'^BotRoster',r'^BotWatch']))
ev=json.load(open(D+'/alloc.json'))['recording']['events']
wt=collections.Counter();T=0
for e in ev:
    v=e['values'];w=v['weight'];T+=w
    for x in set(fr(f) for f in ((v.get('stackTrace') or {}).get('frames') or [])): wt[x]+=w
print('alloc GiB %.1f'%(T/2**30),sorted([(k,round(100*c/T,1)) for k,c in wt.items() if re.search(r'^(Navigator\.plan|SgBot\.tick|MinecraftServer\.athenaTickWorld0|AthenaWorldThread\.tickConnections|RedstoneLampQueue|Scoreboards\.render|CraftWorld\.getBlockTypeIdAt|EntityTracker\.updatePlayers|World\.tickEntities|AthenaBotPlayer\.onUpdate)',k)],key=lambda x:-x[1]))
