"""Remember equipment from the normal opening view and retrieve one reachable ground pickup."""
import math


class MarketSurvey:
    def __init__(self):
        self.items = {}
        self.started = False
        self.stopped = False
        self.done = False
        self.prev_yaw = None
        self.covered = 0.
        self.target = None
        self.expires = 0.
        self.events = []
        self.frames = 0

    def observe(self, bot, obs):
        now, me = obs["t"], obs["self"]
        if self.done:
            return
        if me["weapons"][1] is not None or now>19:
            self.events.append((round(now,2),"armed" if me["weapons"][1] else "expired",me["weapons"][1]["kind"] if me["weapons"][1] else None))
            self.done=True
            return
        for item in obs["pickups"]:
            if item["kind"] not in {"laser","lightning","mortar"}:
                continue
            pos=item["pos"]
            if math.dist(me["pos"][:2],pos[:2])<110. and abs(pos[2]-bot.world.ground(*pos[:2]))<2.5:
                self.items[item["id"]]=dict(item)
        yaw=me["aim"][0]
        if self.prev_yaw is not None and self.started and not self.stopped:
            self.covered+=max(0.,math.remainder(yaw-self.prev_yaw, math.tau))
        self.prev_yaw=yaw

    def danger(self, bot, obs):
        me=obs["self"]
        return (not me["grounded"] or not me["in_zone"] or obs["t"]-bot.last_player_damage_at<4.
                or obs["projectiles"] or obs["beams"]
                or any(m["kind"]=="mech" and math.dist(m["pos"],me["pos"])<100. for m in obs["mechs"])
                or any(s["dist"]=="near" and s["kind"] in {"gun","mortar","lightning","laser","boost","jet"} for s in obs["sounds"]))

    def reserve(self, bot, obs):
        me=obs["self"]
        point=tuple(me["pos"][:2])
        zone=obs["zone"]
        options=[]
        for ident,item in self.items.items():
            goal=tuple(item["pos"][:2])
            straight=math.dist(point,goal)
            if straight>110. or math.dist(goal,zone["center"])>=zone["radius"]-6.:
                continue
            path=bot.world.route(point,goal,probe_limit=350)
            if not path or math.dist(path[-1],goal)>6. or any(math.dist(p,zone["center"])>=zone["radius"]-4. for p in path):
                continue
            total=sum(math.dist(a,b) for a,b in zip([point]+path,path+[goal]))
            travel=1.32*total/me["stats"]["walk_speed"]
            debt=max(0., math.dist(goal,zone["next_center"])-zone["next_radius"]+14.)
            if travel>13. or obs["t"]+travel+12.+2.2*debt/me["stats"]["walk_speed"]>=zone["shrink_end"]:
                continue
            merit=travel-{"laser":1.7,"mortar":.8,"lightning":0.}[item["kind"]]
            options.append((merit,ident,goal,travel,item["kind"]))
        if options:
            _,ident,goal,travel,kind=min(options)
            self.target=(ident,goal)
            self.expires=obs["t"]+travel+3.
            self.events.append((round(obs["t"],2),"route",(ident,kind,goal,round(travel,2))))
        else:
            self.done=True
            self.events.append((round(obs["t"],2),"empty",len(self.items)))

    def route(self, bot, obs, mode, goal, target):
        self.observe(bot,obs)
        if self.done:
            return mode,goal,target
        now,me=obs["t"],obs["self"]
        if self.danger(bot,obs):
            self.events.append((round(now,2),"yield",len(self.items)))
            self.done=True
            return mode,goal,target
        if not self.stopped:
            # The starting wedge already covers the remaining one-third of a revolution.
            completed=self.covered>=math.tau-math.radians(bot.config["vision"]["fov_deg"])-.05
            close=any(math.dist(me["pos"][:2],v["pos"][:2])<=22. for v in self.items.values())
            if not completed and now<1.8 and not close:
                self.started=True
                return mode,goal,target
            self.stopped=True
            self.reserve(bot,obs)
        if self.target is None:
            return mode,goal,target
        ident,point=self.target
        gap=math.dist(me["pos"][:2],point)
        if now>self.expires or gap<9. and not any(r["id"]==ident for r in obs["pickups"]):
            self.events.append((round(now,2),"miss",round(gap,2)))
            self.done=True
            return mode,goal,target
        self.frames+=1
        return "weapon",point,target
