import threading, time, random
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = 'airport_sim_2026'

PLANE_TYPES = ['Passenger', 'Cargo', 'VIP']
PLANE_STATES = ['ON_GROUND','BOARDING','TAXIING','TAKEOFF_QUEUE','TAKING_OFF',
                'IN_AIR','LANDING_QUEUE','LANDING','REFUEL','MAINTENANCE','EMERGENCY','CRASHED']
WEATHER_STATES = ['Sunny','Rain','Fog','Storm','Extreme Weather']
RUNWAY_STATUSES = ['FREE','BUSY','MAINTENANCE','CLOSED_WEATHER']
EMERGENCY_TYPES = ['FIRE','ENGINE_FAILURE','LOW_FUEL','MEDICAL','WEATHER_FAILURE']
DESTINATIONS = ['New York JFK','London LHR','Tokyo NRT','Dubai DXB','Paris CDG',
                'Sydney SYD','Singapore SIN','Frankfurt FRA','Los Angeles LAX','Hong Kong HKG']

FUEL_RATES = {'Passenger': 1.8, 'Cargo': 2.5, 'VIP': 1.2}
SPEED_MAP = {'Passenger': 850, 'Cargo': 750, 'VIP': 900}
WEATHER_FUEL_MULT = {'Sunny':1.0,'Rain':1.15,'Fog':1.05,'Storm':1.35,'Extreme Weather':1.6}
WEATHER_EMERG_PROB = {'Sunny':0.01,'Rain':0.03,'Fog':0.02,'Storm':0.06,'Extreme Weather':0.10}
STATE_DURATIONS = {'BOARDING':3,'TAXIING':2,'TAKING_OFF':1,'LANDING':2,'REFUEL':4,'MAINTENANCE':6}

class Plane:
    def __init__(self, pid, ptype, destination, fuel=100.0):
        self.id = pid
        self.fuel = fuel
        self.plane_type = ptype
        self.state = 'ON_GROUND'
        self.destination = destination
        self.emergency_status = False
        self.emergency_type = None
        self.speed = SPEED_MAP.get(ptype, 800)
        self.fuel_consumption = FUEL_RATES.get(ptype, 2.0)
        self.assigned_runway = None
        self.waiting_time = 0
        self.ticks_in_state = 0
        self.flight_ticks = 0
        self.target_flight_ticks = random.randint(8, 18)

    def to_dict(self):
        return self.__dict__.copy()

class Airport:
    def __init__(self):
        self.planes = {}
        self.runways = {
            'A': {'id':'A','status':'FREE','current_plane':None,'usage_count':0},
            'B': {'id':'B','status':'FREE','current_plane':None,'usage_count':0},
            'C': {'id':'C','status':'FREE','current_plane':None,'usage_count':0},
        }
        self.weather = 'Sunny'
        self.events = []
        self.alerts = []
        self.sim_running = False
        self.sim_paused = False
        self.sim_speed = 1.0
        self.sim_thread = None
        self.lock = threading.Lock()
        self.tick_count = 0
        self.plane_counter = 0
        self.stats = {'total_flights':0,'total_crashes':0,'total_emergencies':0,
                      'total_landings':0,'total_takeoffs':0,'delayed_flights':0,'fuel_alerts':0}

    def log_event(self, plane_id, etype, desc):
        self.events.insert(0, {
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'plane_id': plane_id, 'event_type': etype, 'description': desc
        })
        if len(self.events) > 200:
            self.events = self.events[:200]

    def add_alert(self, msg, severity='info'):
        self.alerts.insert(0, {
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'message': msg, 'severity': severity
        })
        if len(self.alerts) > 50:
            self.alerts = self.alerts[:50]

    def add_plane(self, ptype, destination, fuel=100.0):
        with self.lock:
            self.plane_counter += 1
            pid = f"FL-{self.plane_counter:03d}"
            p = Plane(pid, ptype, destination, fuel)
            self.planes[pid] = p
            self.stats['total_flights'] += 1
            self.log_event(pid, 'ADD', f'{ptype} plane added → {destination}')
            self.add_alert(f'New {ptype} flight {pid} to {destination}', 'info')
            return pid

    def generate_random_planes(self, count=6):
        for _ in range(count):
            pt = random.choice(PLANE_TYPES)
            dest = random.choice(DESTINATIONS)
            fuel = random.uniform(40, 100)
            self.add_plane(pt, dest, round(fuel, 1))

    def get_free_runway(self):
        for rid, rw in self.runways.items():
            if rw['status'] == 'FREE':
                return rid
        return None

    def assign_runway(self, plane_id, runway_id):
        rw = self.runways.get(runway_id)
        if rw and rw['status'] == 'FREE':
            rw['status'] = 'BUSY'
            rw['current_plane'] = plane_id
            rw['usage_count'] += 1
            if plane_id in self.planes:
                self.planes[plane_id].assigned_runway = runway_id
            return True
        return False

    def release_runway(self, runway_id):
        rw = self.runways.get(runway_id)
        if rw:
            old_plane = rw['current_plane']
            if old_plane and old_plane in self.planes:
                self.planes[old_plane].assigned_runway = None
            rw['status'] = 'FREE'
            rw['current_plane'] = None

    def force_runway_for_emergency(self, plane_id):
        free = self.get_free_runway()
        if free:
            self.assign_runway(plane_id, free)
            return free
        for rid, rw in self.runways.items():
            if rw['status'] == 'BUSY':
                bumped = rw['current_plane']
                self.release_runway(rid)
                if bumped and bumped in self.planes:
                    bp = self.planes[bumped]
                    if bp.state in ('TAKING_OFF','TAKEOFF_QUEUE'):
                        bp.state = 'TAKEOFF_QUEUE'
                        bp.ticks_in_state = 0
                    elif bp.state == 'LANDING':
                        bp.state = 'LANDING_QUEUE'
                        bp.ticks_in_state = 0
                    self.log_event(bumped, 'BUMPED', f'Bumped from runway {rid} for emergency')
                self.assign_runway(plane_id, rid)
                return rid
        return None

    def change_state(self, plane, new_state):
        old = plane.state
        plane.state = new_state
        plane.ticks_in_state = 0
        self.log_event(plane.id, 'STATE', f'{old} → {new_state}')

    def update_weather(self):
        if random.random() < 0.06:
            old = self.weather
            weights = [35, 25, 20, 15, 5]
            self.weather = random.choices(WEATHER_STATES, weights=weights, k=1)[0]
            if self.weather != old:
                self.log_event('SYSTEM', 'WEATHER', f'Weather changed: {old} → {self.weather}')
                self.add_alert(f'Weather: {self.weather}',
                    'danger' if self.weather in ('Storm','Extreme Weather') else 'warning' if self.weather in ('Rain','Fog') else 'success')
                self.apply_weather_to_runways()

    def apply_weather_to_runways(self):
        if self.weather == 'Extreme Weather':
            for rid, rw in self.runways.items():
                if rw['status'] != 'BUSY':
                    rw['status'] = 'CLOSED_WEATHER'
                    self.log_event('SYSTEM', 'RUNWAY', f'Runway {rid} closed - extreme weather')
        elif self.weather == 'Storm':
            closed = random.choice(list(self.runways.keys()))
            rw = self.runways[closed]
            if rw['status'] != 'BUSY':
                rw['status'] = 'CLOSED_WEATHER'
                self.log_event('SYSTEM', 'RUNWAY', f'Runway {closed} closed - storm')
            for rid, r in self.runways.items():
                if rid != closed and r['status'] == 'CLOSED_WEATHER':
                    r['status'] = 'FREE'
        else:
            for rid, rw in self.runways.items():
                if rw['status'] == 'CLOSED_WEATHER':
                    rw['status'] = 'FREE'
                    self.log_event('SYSTEM', 'RUNWAY', f'Runway {rid} reopened')

    def update_fuel(self):
        mult = WEATHER_FUEL_MULT.get(self.weather, 1.0)
        for p in self.planes.values():
            if p.state == 'IN_AIR':
                p.fuel -= p.fuel_consumption * mult
                p.fuel = max(0, round(p.fuel, 1))
                p.flight_ticks += 1
            elif p.state in ('TAXIING','TAKING_OFF'):
                p.fuel -= 0.3
                p.fuel = max(0, round(p.fuel, 1))

    def check_fuel(self):
        for p in self.planes.values():
            if p.state == 'CRASHED':
                continue
            if p.fuel <= 0 and p.state != 'ON_GROUND':
                self.change_state(p, 'CRASHED')
                self.stats['total_crashes'] += 1
                self.log_event(p.id, 'CRASH', f'{p.plane_type} crashed - fuel depleted!')
                self.add_alert(f'CRASH: {p.id} fuel depleted!', 'danger')
                if p.assigned_runway:
                    self.release_runway(p.assigned_runway)
            elif p.fuel < 15 and not p.emergency_status and p.state == 'IN_AIR':
                p.emergency_status = True
                p.emergency_type = 'LOW_FUEL'
                self.change_state(p, 'EMERGENCY')
                self.stats['total_emergencies'] += 1
                self.stats['fuel_alerts'] += 1
                self.add_alert(f'EMERGENCY: {p.id} critical fuel ({p.fuel}%)', 'danger')
            elif p.fuel < 30 and p.state == 'IN_AIR':
                self.stats['fuel_alerts'] += 1
                self.add_alert(f'WARNING: {p.id} low fuel ({p.fuel}%)', 'warning')

    def process_emergencies(self):
        for p in list(self.planes.values()):
            if p.state == 'EMERGENCY' and not p.assigned_runway:
                rid = self.force_runway_for_emergency(p.id)
                if rid:
                    self.change_state(p, 'LANDING')
                    self.log_event(p.id, 'EMERGENCY_LAND', f'Emergency landing on runway {rid}')
                    self.add_alert(f'EMERGENCY LANDING: {p.id} on runway {rid}', 'danger')

    def process_queues(self):
        weather = self.weather
        landing = sorted(
            [p for p in self.planes.values() if p.state == 'LANDING_QUEUE'],
            key=lambda x: (-int(x.emergency_status), x.fuel, -int(x.plane_type=='VIP'), -x.waiting_time)
        )
        for p in landing:
            rid = self.get_free_runway()
            if rid:
                self.assign_runway(p.id, rid)
                self.change_state(p, 'LANDING')
                self.log_event(p.id, 'LANDING', f'Landing on runway {rid}')

        if weather not in ('Storm', 'Extreme Weather'):
            takeoff = sorted(
                [p for p in self.planes.values() if p.state == 'TAKEOFF_QUEUE'],
                key=lambda x: (-int(x.plane_type=='VIP'), -x.waiting_time)
            )
            for p in takeoff:
                rid = self.get_free_runway()
                if rid:
                    self.assign_runway(p.id, rid)
                    self.change_state(p, 'TAKING_OFF')
                    self.log_event(p.id, 'TAKEOFF', f'Taking off from runway {rid}')
        else:
            for p in self.planes.values():
                if p.state == 'TAKEOFF_QUEUE':
                    p.waiting_time += 1
                    self.stats['delayed_flights'] += 1

    def advance_states(self):
        rain_delay = 1 if self.weather == 'Rain' else 0
        for p in list(self.planes.values()):
            if p.state == 'CRASHED':
                continue
            p.ticks_in_state += 1
            if p.state in ('TAKEOFF_QUEUE','LANDING_QUEUE'):
                p.waiting_time += 1

            dur = STATE_DURATIONS.get(p.state, 999)
            if p.state in ('BOARDING','TAXIING','LANDING'):
                dur += rain_delay

            if p.state == 'BOARDING' and p.ticks_in_state >= dur:
                self.change_state(p, 'TAXIING')
            elif p.state == 'TAXIING' and p.ticks_in_state >= dur:
                self.change_state(p, 'TAKEOFF_QUEUE')
            elif p.state == 'TAKING_OFF' and p.ticks_in_state >= dur:
                self.change_state(p, 'IN_AIR')
                self.stats['total_takeoffs'] += 1
                if p.assigned_runway:
                    self.release_runway(p.assigned_runway)
                self.add_alert(f'{p.id} is now airborne → {p.destination}', 'success')
            elif p.state == 'IN_AIR' and p.flight_ticks >= p.target_flight_ticks:
                self.change_state(p, 'LANDING_QUEUE')
                self.log_event(p.id, 'ARRIVING', f'Approaching airport from {p.destination}')
            elif p.state == 'LANDING' and p.ticks_in_state >= dur:
                self.change_state(p, 'ON_GROUND')
                p.emergency_status = False
                p.emergency_type = None
                p.flight_ticks = 0
                self.stats['total_landings'] += 1
                if p.assigned_runway:
                    self.release_runway(p.assigned_runway)
                self.add_alert(f'{p.id} landed safely', 'success')
            elif p.state == 'REFUEL' and p.ticks_in_state >= dur:
                p.fuel = 100.0
                self.change_state(p, 'ON_GROUND')
                self.add_alert(f'{p.id} refueled to 100%', 'success')
            elif p.state == 'MAINTENANCE' and p.ticks_in_state >= dur:
                self.change_state(p, 'ON_GROUND')
                self.add_alert(f'{p.id} maintenance complete', 'info')

    def auto_prepare(self):
        idle = [p for p in self.planes.values()
                if p.state == 'ON_GROUND' and p.fuel > 30 and p.ticks_in_state > 4
                and not p.emergency_status]
        if idle and random.random() < 0.3:
            p = random.choice(idle)
            self.change_state(p, 'BOARDING')
            self.log_event(p.id, 'AUTO', f'Auto-boarding for {p.destination}')

    def random_events(self):
        ep = WEATHER_EMERG_PROB.get(self.weather, 0.01)
        for p in list(self.planes.values()):
            if p.state == 'IN_AIR' and not p.emergency_status and random.random() < ep:
                etype = random.choice(['FIRE','ENGINE_FAILURE','MEDICAL','WEATHER_FAILURE'])
                p.emergency_status = True
                p.emergency_type = etype
                self.change_state(p, 'EMERGENCY')
                self.stats['total_emergencies'] += 1
                self.log_event(p.id, 'EMERGENCY', f'{etype} emergency!')
                self.add_alert(f'EMERGENCY: {p.id} - {etype}', 'danger')
                break
        if random.random() < 0.08 and len(self.planes) < 20:
            pt = random.choice(PLANE_TYPES)
            dest = random.choice(DESTINATIONS)
            fuel = random.uniform(25, 70)
            self.plane_counter += 1
            pid = f"FL-{self.plane_counter:03d}"
            p = Plane(pid, pt, dest, round(fuel, 1))
            p.state = 'LANDING_QUEUE'
            p.flight_ticks = p.target_flight_ticks
            self.planes[pid] = p
            self.stats['total_flights'] += 1
            self.log_event(pid, 'ARRIVAL', f'Incoming {pt} from {dest}')
            self.add_alert(f'Incoming flight {pid} from {dest}', 'info')

    def simulation_tick(self):
        with self.lock:
            self.tick_count += 1
            self.update_weather()
            self.update_fuel()
            self.check_fuel()
            self.process_emergencies()
            self.advance_states()
            self.process_queues()
            self.auto_prepare()
            self.random_events()

    def run_simulation(self):
        while self.sim_running:
            if not self.sim_paused:
                self.simulation_tick()
            delay = max(0.5, 3.0 / self.sim_speed)
            time.sleep(delay)

    def start_sim(self):
        if not self.sim_running:
            self.sim_running = True
            self.sim_paused = False
            self.sim_thread = threading.Thread(target=self.run_simulation, daemon=True)
            self.sim_thread.start()
            self.log_event('SYSTEM', 'SIM', 'Simulation started')
            self.add_alert('Simulation started', 'success')

    def pause_sim(self):
        self.sim_paused = True
        self.log_event('SYSTEM', 'SIM', 'Simulation paused')
        self.add_alert('Simulation paused', 'warning')

    def resume_sim(self):
        self.sim_paused = False
        self.log_event('SYSTEM', 'SIM', 'Simulation resumed')
        self.add_alert('Simulation resumed', 'success')

    def stop_sim(self):
        self.sim_running = False
        self.sim_paused = False
        self.log_event('SYSTEM', 'SIM', 'Simulation stopped')
        self.add_alert('Simulation stopped', 'warning')

    def reset(self):
        with self.lock:
            self.stop_sim()
            self.planes.clear()
            for rw in self.runways.values():
                rw['status'] = 'FREE'
                rw['current_plane'] = None
                rw['usage_count'] = 0
            self.weather = 'Sunny'
            self.events.clear()
            self.alerts.clear()
            self.tick_count = 0
            self.plane_counter = 0
            self.stats = {k: 0 for k in self.stats}

    def get_stats(self):
        with self.lock:
            active = len([p for p in self.planes.values() if p.state == 'IN_AIR'])
            emergency = len([p for p in self.planes.values() if p.state == 'EMERGENCY'])
            crashed = len([p for p in self.planes.values() if p.state == 'CRASHED'])
            grounded = len([p for p in self.planes.values() if p.state == 'ON_GROUND'])
            in_queue = len([p for p in self.planes.values() if p.state in ('TAKEOFF_QUEUE','LANDING_QUEUE')])
            busy_rw = len([r for r in self.runways.values() if r['status'] == 'BUSY'])
            free_rw = len([r for r in self.runways.values() if r['status'] == 'FREE'])
            closed_rw = len([r for r in self.runways.values() if r['status'] == 'CLOSED_WEATHER'])
            avg_wait = 0
            waiters = [p.waiting_time for p in self.planes.values() if p.waiting_time > 0]
            if waiters:
                avg_wait = round(sum(waiters) / len(waiters), 1)
            return {**self.stats, 'active_flights': active, 'emergency_now': emergency,
                    'crashed_now': crashed, 'grounded': grounded, 'in_queue': in_queue,
                    'busy_runways': busy_rw, 'free_runways': free_rw, 'closed_runways': closed_rw,
                    'avg_waiting': avg_wait, 'total_planes': len(self.planes), 'tick': self.tick_count}

    def search_planes(self, query='', filt='all'):
        results = list(self.planes.values())
        if query:
            q = query.upper()
            results = [p for p in results if q in p.id.upper() or q in p.plane_type.upper()
                       or q in p.destination.upper() or q in p.state.upper()]
        if filt == 'emergency':
            results = [p for p in results if p.emergency_status]
        elif filt == 'in_air':
            results = [p for p in results if p.state == 'IN_AIR']
        elif filt == 'waiting':
            results = [p for p in results if p.state in ('TAKEOFF_QUEUE','LANDING_QUEUE')]
        elif filt == 'crashed':
            results = [p for p in results if p.state == 'CRASHED']
        elif filt == 'ground':
            results = [p for p in results if p.state == 'ON_GROUND']
        return results

airport = Airport()

def get_refresh():
    if airport.sim_running and not airport.sim_paused:
        return max(1, int(3 / airport.sim_speed))
    return 0

@app.route('/')
def dashboard():
    stats = airport.get_stats()
    return render_template('dashboard.html', airport=airport, stats=stats,
                           refresh=get_refresh(), page='dashboard')

@app.route('/planes')
def planes():
    q = request.args.get('q', '')
    f = request.args.get('filter', 'all')
    results = airport.search_planes(q, f)
    return render_template('planes.html', planes=results, query=q, filt=f,
                           airport=airport, refresh=get_refresh(), page='planes')

@app.route('/planes/add', methods=['GET','POST'])
def add_plane():
    if request.method == 'POST':
        pt = request.form.get('plane_type', 'Passenger')
        dest = request.form.get('destination', 'Unknown')
        fuel = float(request.form.get('fuel', 100))
        airport.add_plane(pt, dest, fuel)
        flash(f'Plane added successfully!', 'success')
        return redirect(url_for('planes'))
    return render_template('add_plane.html', types=PLANE_TYPES,
                           destinations=DESTINATIONS, airport=airport, refresh=0, page='planes')

@app.route('/plane/<pid>/action', methods=['POST'])
def plane_action(pid):
    action = request.form.get('action', '')
    with airport.lock:
        p = airport.planes.get(pid)
        if not p:
            flash('Plane not found', 'danger')
            return redirect(url_for('planes'))
        if action == 'prepare' and p.state == 'ON_GROUND':
            airport.change_state(p, 'BOARDING')
            flash(f'{pid} preparing for takeoff', 'success')
        elif action == 'refuel' and p.state == 'ON_GROUND':
            airport.change_state(p, 'REFUEL')
            flash(f'{pid} refueling', 'info')
        elif action == 'maintenance' and p.state == 'ON_GROUND':
            airport.change_state(p, 'MAINTENANCE')
            flash(f'{pid} in maintenance', 'info')
        elif action == 'emergency':
            etype = request.form.get('emergency_type', 'FIRE')
            p.emergency_status = True
            p.emergency_type = etype
            airport.change_state(p, 'EMERGENCY')
            airport.stats['total_emergencies'] += 1
            airport.add_alert(f'EMERGENCY: {pid} - {etype}', 'danger')
            flash(f'Emergency triggered for {pid}', 'danger')
        elif action == 'remove' and p.state in ('ON_GROUND','CRASHED'):
            if p.assigned_runway:
                airport.release_runway(p.assigned_runway)
            del airport.planes[pid]
            airport.log_event(pid, 'REMOVE', 'Plane removed')
            flash(f'{pid} removed', 'info')
    return redirect(url_for('planes'))

@app.route('/runways')
def runways():
    return render_template('runways.html', airport=airport, refresh=get_refresh(), page='runways')

@app.route('/weather')
def weather():
    return render_template('weather.html', airport=airport, refresh=get_refresh(), page='weather')

@app.route('/history')
def history():
    etype = request.args.get('type', 'all')
    events = airport.events
    if etype != 'all':
        events = [e for e in events if e['event_type'] == etype]
    etypes = list(set(e['event_type'] for e in airport.events))
    return render_template('history.html', events=events[:100], event_types=sorted(etypes),
                           current_type=etype, airport=airport, refresh=get_refresh(), page='history')

@app.route('/analytics')
def analytics():
    stats = airport.get_stats()
    rw_data = airport.runways
    return render_template('analytics.html', stats=stats, runways=rw_data,
                           airport=airport, refresh=get_refresh(), page='analytics')

@app.route('/sim/<action>', methods=['POST'])
def sim_control(action):
    if action == 'start':
        airport.start_sim()
    elif action == 'pause':
        airport.pause_sim()
    elif action == 'resume':
        airport.resume_sim()
    elif action == 'stop':
        airport.stop_sim()
    elif action == 'reset':
        airport.reset()
        flash('Airport reset', 'info')
    elif action == 'generate':
        airport.generate_random_planes(6)
        flash('Generated 6 random planes', 'success')
    elif action == 'speed':
        s = float(request.form.get('speed', 1))
        airport.sim_speed = max(0.5, min(5, s))
        flash(f'Speed set to {airport.sim_speed}x', 'info')
    return redirect(request.referrer or url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
