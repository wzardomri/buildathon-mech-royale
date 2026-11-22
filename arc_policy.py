"""Use an idle secondary on a confirmed nearby drone along Prism's existing aim."""
import math

def angle_delta(a,b):
    return math.remainder(a-b,math.tau)

class NeutralArc:
    def __init__(self):
        self.known_house=set()
        self.eliminated=set()
        self.last_house={}
        self.neutral_damage={}
        self.paid=False
        self.first=None
        self.pulses=0

    def update(self,obs):
        now=obs['t']
        self.known_house.update(m['id'] for m in obs['mechs'] if m['kind']=='mech')
        for e in obs['events']:
            kind=e['type']
            if kind=='kill' and e.get('target') in self.known_house:
                self.paid=True
                self.eliminated.add(e['target'])
            if kind=='damaged' and e.get('weapon') in ('npc','npc_gun') and e.get('by') is not None:
                key=e['by']
                times=self.neutral_damage.get(key,())
                self.neutral_damage[key]=(*times[-1:],now)
            if kind=='damaged' and e.get('by') in self.known_house:
                self.last_house[e['by']]=now
            if kind=='hit' and e.get('target') in self.known_house:
                self.last_house[e['target']]=now

    def choose(self,obs,action,state):
        self.update(obs)
        if not self.paid or state.last_policy!='base' or state.combat_target is not None:
            return None
        if any(action.get('fire',())) or action.get('take') is not None or action.get('drop') is not None or action.get('pick') is not None:
            return None
        now=obs['t']
        if any(now-t<2.4 for i,t in self.last_house.items() if i not in self.eliminated):
            return None
        if obs['beams'] or any(p['kind'] not in ('npc','npc_gun') for p in obs['projectiles']) or any(m['kind']=='mech' for m in obs['mechs']):
            return None
        me=obs['self']
        sec=me['weapons'][1]
        if not sec or sec['kind']!='lightning' or sec['cooldown']>.05 or not me['grounded']:
            return None
        stats=sec['stats'];ms=me['stats']
        origin=(*me['pos'][:2],me['pos'][2]+ms['muzzle_height'])
        yaw=me['aim'][0]
        yaw_step=max(-.1*ms['turn_rate'],min(.1*ms['turn_rate'],angle_delta(action['aim'][0],yaw)))
        end_yaw=yaw+yaw_step
        angle=math.radians(stats['cone_deg']*.5)-math.radians(2)
        visible=[*obs['mechs'],*obs['crates']]
        for drone in obs['mechs']:
            ident=drone['id'];recent=self.neutral_damage.get(ident,())
            if drone['kind']!='npc' or len(recent)!=2 or now-recent[-1]>2 or recent[-1]-recent[0]>3:
                continue
            target=(*drone['pos'][:2],drone['pos'][2]+state.rules['npc']['height']*.52)
            distance=math.dist(origin,target)
            bearing=math.atan2(target[1]-origin[1],target[0]-origin[0])
            if distance>stats['range']-1 or max(abs(angle_delta(bearing,u))for u in (yaw,end_yaw))>angle:
                continue
            if state.world.blocked_between(origin,target):
                continue
            # A closer body could receive the first arc or chain through a new neutral.
            def competes(v):
                if v['id']==ident:return False
                p=v['pos']
                if math.dist(p[:2],drone['pos'][:2])<=stats['chain_range']+1:return True
                if math.dist(origin[:2],p[:2])>distance+2:return False
                vb=math.atan2(p[1]-origin[1],p[0]-origin[0])
                return min(abs(angle_delta(vb,u))for u in (yaw,end_yaw))<=math.radians(stats['cone_deg']+3)
            if any(competes(v) for v in visible):
                continue
            cubes=state.rules['cubes']
            damage=stats['damage']*(1+min(cubes['stat_cap'],me['cubes'])*cubes['damage_per_cube'])
            count=max(1,math.ceil(drone['hp']/damage))
            duration=(count-1)*stats['cooldown']+.1
            motion_cost=duration*(ms['boost_drain']*bool(action.get('boost'))+ms['jet_drain']*bool(action.get('jump')))
            if me['energy']<count*stats['energy']+motion_cost+.40*me['max_energy']:
                continue
            self.pulses+=1
            if self.first is None:self.first=(now,ident,distance)
            return dict(action,fire=[False,True])
        return None
