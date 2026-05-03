import React, { useEffect, useRef, useState, useCallback } from 'react';

const WS_URL = 'ws://localhost:8000/ws';
const MAP_BOUNDS = { xMin: -50, xMax: 180, yMin: -40, yMax: 120 };

// Color scheme: sensors uniform cyan, tracks uniform amber.
// Different shapes encode subtype.
const COLOR_SENSOR = '#3ad7ff';
const COLOR_TRACK = '#ffb347';
const COLOR_TRACK_FAINT = '#ffb34755';
const COLOR_GRID = '#0e1a26';
const COLOR_GRID_LABEL = '#26405c';
const COLOR_BG = '#06090f';

function trackConfidence(track) {
  return track?.track_confidence ?? track?.existence ?? 0;
}

function bestProbEntry(scores, fallbackName = 'Unknown', fallbackProb = 0) {
  const entries = Object.entries(scores || {}).sort((a, b) => b[1] - a[1]);
  return entries[0] || [fallbackName, fallbackProb];
}

function formatPct(value, digits = 0) {
  const pct = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  return `${(pct * 100).toFixed(digits)}%`;
}

// ---------------------------------------------------------------- App

export default function App() {
  const canvasRef = useRef(null);
  const wsRef = useRef(null);
  const stateRef = useRef(null);
  const [, forceTick] = useState(0);
  const [connected, setConnected] = useState(false);
  const [hover, setHover] = useState(null); // {kind: 'sensor'|'track', id: string, x: number, y: number}
  const [pinned, setPinned] = useState(null);
  const [cycle, setCycle] = useState(0);

  const connectWs = useCallback(() => {
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => { setConnected(false); setTimeout(connectWs, 2000); };
    ws.onerror = () => ws.close();
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === 'update') {
        stateRef.current = msg.data;
        setCycle(msg.data.cycle);
        forceTick(t => t + 1);
      } else if (msg.type === 'scenario') {
        if (!stateRef.current) {
          stateRef.current = { sensors: msg.data.sensors, tracks: [], cycle: 0 };
          forceTick(t => t + 1);
        }
      }
    };
  }, []);
  useEffect(() => { connectWs(); return () => wsRef.current?.close(); }, [connectWs]);

  useEffect(() => {
    const handleResize = () => {
      if (canvasRef.current) {
        canvasRef.current.width = window.innerWidth;
        canvasRef.current.height = window.innerHeight;
        forceTick(t => t + 1);
      }
    };
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    if (canvasRef.current && stateRef.current) {
      drawTacticalDisplay(canvasRef.current, stateRef.current, hover, pinned);
    }
  });

  const onMouseMove = (e) => {
    if (!canvasRef.current || !stateRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const hit = pickEntity(canvasRef.current, stateRef.current, px, py);
    if (hit) setHover({ ...hit, screenX: px, screenY: py });
    else setHover(null);
  };
  const onClick = () => {
    if (hover) setPinned(hover);
    else setPinned(null);
  };

  const handleReset = () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ command: 'reset' }));
    }
    setPinned(null);
  };

  const inspect = pinned || hover;
  const state = stateRef.current;

  return (
    <div style={{ width: '100vw', height: '100vh', position: 'relative', overflow: 'hidden' }}>
      <canvas
        ref={canvasRef}
        onMouseMove={onMouseMove}
        onClick={onClick}
        style={{ display: 'block', width: '100%', height: '100%', cursor: hover ? 'pointer' : 'crosshair' }}
      />
      <Hud connected={connected} cycle={cycle} state={state} onReset={handleReset} />
      <Legend />
      {inspect && state && <InspectPanel
        kind={inspect.kind}
        entity={inspect.kind === 'sensor'
          ? state.sensors.find(s => s.sensor_id === inspect.id)
          : state.tracks.find(t => t.track_id === inspect.id)}
        pinned={!!pinned}
        onClose={() => setPinned(null)}
      />}
    </div>
  );
}

// ---------------------------------------------------------------- HUD

function Hud({ connected, cycle, state, onReset }) {
  const tracks = state ? state.tracks.filter(t => trackConfidence(t) > 0.05) : [];
  return (
    <div style={{
      position: 'absolute', top: 10, left: 10, color: '#cfe6f5',
      fontFamily: 'Consolas, Courier New, monospace', fontSize: 12,
      background: 'rgba(8,12,20,0.85)', padding: '10px 14px', borderRadius: 4,
      border: '1px solid #1d3650',
    }}>
      <div style={{ fontSize: 13, fontWeight: 'bold', color: COLOR_SENSOR, marginBottom: 6, letterSpacing: 1 }}>
        TACTICAL FUSION DISPLAY
      </div>
      <div>Link: {connected
        ? <span style={{color:'#58e07a'}}>ONLINE</span>
        : <span style={{color:'#ff5566'}}>OFFLINE</span>}</div>
      <div>Cycle: <span style={{color:'#ffd27a'}}>{cycle}</span></div>
      <div>Tracks: <span style={{color:'#ffd27a'}}>{tracks.length}</span></div>
      <button onClick={onReset} style={{
        marginTop: 8, padding: '4px 10px', background: '#10283c', color: COLOR_SENSOR,
        border: '1px solid #1d4a6b', cursor: 'pointer', fontFamily: 'inherit',
        fontSize: 11, letterSpacing: 1,
      }}>RESET</button>
      <div style={{ marginTop: 8, fontSize: 10, color: '#557080' }}>
        Hover anything for details. Click to pin.
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div style={{
      position: 'absolute', bottom: 10, left: 10, color: '#92aabd',
      fontFamily: 'Consolas, Courier New, monospace', fontSize: 11,
      background: 'rgba(8,12,20,0.85)', padding: '10px 14px', borderRadius: 4,
      border: '1px solid #1d3650', minWidth: 180,
    }}>
      <div style={{ fontWeight: 'bold', marginBottom: 6, color: COLOR_SENSOR }}>SENSORS</div>
      <LegendRow><SensorIcon kind="radar" />Radar</LegendRow>
      <LegendRow><SensorIcon kind="camera" />Camera (EO/IR)</LegendRow>
      <LegendRow><SensorIcon kind="sigint" />SIGINT</LegendRow>

      <div style={{ fontWeight: 'bold', marginTop: 10, marginBottom: 6, color: COLOR_TRACK }}>OBJECTS</div>
      <LegendRow><ClassIcon klass="SAM Battery" />SAM Battery</LegendRow>
      <LegendRow><ClassIcon klass="Radar Station" />Radar Station</LegendRow>
      <LegendRow><ClassIcon klass="Command Post" />Command Post</LegendRow>
      <LegendRow><ClassIcon klass="Vehicle Convoy" />Vehicle Convoy</LegendRow>
      <LegendRow><ClassIcon klass="Infantry" />Infantry</LegendRow>
      <LegendRow><ClassIcon klass="Aircraft" />Aircraft</LegendRow>
      <LegendRow><ClassIcon klass="Unknown" />Unknown</LegendRow>
    </div>
  );
}

function LegendRow({ children }) {
  return <div style={{ display: 'flex', alignItems: 'center', marginBottom: 2 }}>{children}</div>;
}

function SensorIcon({ kind }) {
  return (
    <svg width="14" height="14" style={{ marginRight: 6 }}>
      <SensorShape kind={kind} cx={7} cy={7} size={5} />
    </svg>
  );
}

function ClassIcon({ klass }) {
  return (
    <svg width="14" height="14" style={{ marginRight: 6 }}>
      <ClassShape klass={klass} cx={7} cy={7} size={5} />
    </svg>
  );
}

function SensorShape({ kind, cx, cy, size }) {
  const stroke = COLOR_SENSOR, fill = COLOR_SENSOR + '88';
  if (kind === 'radar') {
    // Up-pointing triangle
    return <polygon points={`${cx},${cy-size} ${cx+size},${cy+size*0.7} ${cx-size},${cy+size*0.7}`} fill={fill} stroke={stroke} />;
  }
  if (kind === 'camera') {
    // Square
    return <rect x={cx-size} y={cy-size} width={size*2} height={size*2} fill={fill} stroke={stroke} />;
  }
  if (kind === 'sigint') {
    // Diamond
    return <polygon points={`${cx},${cy-size} ${cx+size},${cy} ${cx},${cy+size} ${cx-size},${cy}`} fill={fill} stroke={stroke} />;
  }
  return null;
}

function ClassShape({ klass, cx, cy, size }) {
  const stroke = COLOR_TRACK, fill = COLOR_TRACK + 'aa';
  switch (klass) {
    case 'SAM Battery':
      return <polygon points={`${cx},${cy-size} ${cx+size},${cy} ${cx},${cy+size} ${cx-size},${cy}`} fill={fill} stroke={stroke} />;
    case 'Radar Station':
      return <rect x={cx-size} y={cy-size} width={size*2} height={size*2} fill={fill} stroke={stroke} />;
    case 'Command Post':
      return <polygon points={hexPts(cx, cy, size)} fill={fill} stroke={stroke} />;
    case 'Vehicle Convoy':
      return <polygon points={`${cx-size*0.9},${cy-size} ${cx-size*0.9},${cy+size} ${cx+size},${cy}`} fill={fill} stroke={stroke} />;
    case 'Infantry':
      return <circle cx={cx} cy={cy} r={size*0.85} fill={fill} stroke={stroke} />;
    case 'Aircraft':
      return <polygon points={`${cx},${cy-size} ${cx+size},${cy+size*0.7} ${cx-size},${cy+size*0.7}`} fill={fill} stroke={stroke} />;
    default:
      return <g><circle cx={cx} cy={cy} r={size} fill="none" stroke={stroke}/><text x={cx} y={cy+size*0.5} fontSize={size+2} textAnchor="middle" fill={stroke}>?</text></g>;
  }
}

function hexPts(cx, cy, s) {
  let pts = [];
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2 + Math.PI / 6;
    pts.push(`${cx + Math.cos(a) * s},${cy + Math.sin(a) * s}`);
  }
  return pts.join(' ');
}

// ---------------------------------------------------------------- Inspect panel

function InspectPanel({ kind, entity, pinned, onClose }) {
  if (!entity) return null;
  return (
    <div style={{
      position: 'absolute', top: 10, right: 10, width: 320, color: '#cfe6f5',
      fontFamily: 'Consolas, Courier New, monospace', fontSize: 11,
      background: 'rgba(8,12,20,0.92)', padding: '12px 14px', borderRadius: 4,
      border: `1px solid ${kind === 'sensor' ? COLOR_SENSOR + '88' : COLOR_TRACK + '88'}`,
      maxHeight: 'calc(100vh - 20px)', overflowY: 'auto',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontWeight: 'bold', color: kind === 'sensor' ? COLOR_SENSOR : COLOR_TRACK, letterSpacing: 1 }}>
          {kind === 'sensor' ? entity.sensor_id : entity.track_id}{pinned ? ' · pinned' : ''}
        </span>
        {pinned && <button onClick={onClose} style={{
          background: 'transparent', color: '#888', border: '1px solid #444',
          cursor: 'pointer', fontSize: 10, padding: '2px 6px',
        }}>×</button>}
      </div>
      {kind === 'sensor' ? <SensorPanel sensor={entity} /> : <TrackPanel track={entity} />}
    </div>
  );
}

function SensorPanel({ sensor }) {
  // Sensor obs come from the state's tracks (each track's last_observations);
  // we can't easily get them per-sensor here without extra state. For now,
  // show static info + per-cycle observation count from any track that
  // contributed this cycle. This panel is mounted with stateRef so we can
  // dig into the live snapshot below.
  return (
    <>
      <Section title="SENSOR">
        <Row k="ID" v={sensor.sensor_id} />
        <Row k="Type" v={sensor.sensor_type.toUpperCase()} />
        <Row k="Position" v={`(${sensor.position.x.toFixed(0)}, ${sensor.position.y.toFixed(0)}) km`} />
        <Row k="Max range" v={`${sensor.max_range_km.toFixed(0)} km`} />
      </Section>
      <Section title="OUTPUT TYPE">
        {sensor.sensor_type === 'radar' && (
          <div style={{ color: '#aac', fontSize: 10 }}>
            Reports bearing + range + RCS + range-rate.<br />
            <span style={{ color: '#778' }}>1.5° bearing, ~1% range error.</span>
          </div>
        )}
        {sensor.sensor_type === 'camera' && (
          <div style={{ color: '#aac', fontSize: 10 }}>
            Reports bearing + class-likelihood vector.<br />
            <span style={{ color: '#778' }}>0.5° bearing, no range.</span>
          </div>
        )}
        {sensor.sensor_type === 'sigint' && (
          <div style={{ color: '#aac', fontSize: 10 }}>
            Reports bearing + emission type + signal strength.<br />
            <span style={{ color: '#778' }}>4° bearing, no range, only emitting targets.</span>
          </div>
        )}
      </Section>
      <SensorCurrentReadings sensorId={sensor.sensor_id} />
    </>
  );
}

function SensorCurrentReadings({ sensorId }) {
  // Look up the current state snapshot from window globals (set by drawTacticalDisplay)
  const state = window.__sensorPipelineState;
  if (!state) return null;
  const lines = [];
  for (const tr of state.tracks || []) {
    if (!tr.last_observations) continue;
    for (const r of tr.last_observations) {
      if (r.sensor_id === sensorId) {
        lines.push({ track: tr.track_id, ...r, target_class: tr.leading_class });
      }
    }
  }
  if (lines.length === 0) {
    return (
      <Section title="CURRENT READING">
        <div style={{ color: '#778', fontSize: 10, fontStyle: 'italic' }}>No detections this cycle.</div>
      </Section>
    );
  }
  return (
    <Section title={`CURRENT READING (${lines.length})`}>
      {lines.map((r, i) => (
        <div key={i} style={{ marginBottom: 6, padding: '4px 6px', background: '#0c1828', borderRadius: 3 }}>
          <div style={{ fontSize: 10, color: COLOR_TRACK, marginBottom: 2 }}>{r.track} <span style={{color:'#667', fontSize:9}}>({r.target_class})</span></div>
          <div style={{ fontSize: 10, color: '#aac' }}>
            bearing {r.bearing.toFixed(1)}°
            {r.range_km != null && <> · range {r.range_km.toFixed(1)} km</>}
            {r.rcs != null && <> · RCS {r.rcs.toFixed(1)} m²</>}
            {r.emission_type && <> · {r.emission_type}</>}
          </div>
        </div>
      ))}
    </Section>
  );
}

function TrackPanel({ track }) {
  const pu = track.position_uncertainty;
  const confidence = trackConfidence(track);
  const classFallback = confidence > 0 ? (track.leading_class_prob || 0) / confidence : 0;
  const categoryFallback = confidence > 0 ? ((track.leading_category_prob ?? track.leading_type_prob ?? 0) / confidence) : 0;
  const [className, classProb] = bestProbEntry(track.class_probs_conditional, track.leading_class || 'Unknown', classFallback);
  const [categoryName, categoryProb] = bestProbEntry(
    track.category_probs_conditional,
    track.leading_category || track.leading_type || 'Unknown',
    categoryFallback
  );
  const currentlyDetected = track.currently_detected ?? track.currently_observed ?? false;
  const cyclesSince = Math.max(0, Math.round(track.cycles_since_last_seen ?? 0));
  const lastSeen = currentlyDetected ? 'this cycle' : `${cyclesSince} cycle${cyclesSince === 1 ? '' : 's'} ago`;
  const sensors = currentlyDetected ? (track.contributing_sensor_ids || []) : [];
  const positionMode = currentlyDetected ? 'current' : 'predicted';
  return (
    <>
      <Section title="TRACK">
        <MetricReadout label="Track Confidence" value={confidence} />
        <SensorTrackingReadout sensors={sensors} />
        <Row k="Last seen" v={lastSeen} />
      </Section>

      <Section title="IDENTITY">
        <MetricReadout label={`Category: ${categoryName}`} value={categoryProb} />
        <MetricReadout label={`Classification: ${className}`} value={classProb} />
      </Section>

      <Section title="POSITION">
        <Row k="Estimate" v={`${positionMode} (${track.position.x.toFixed(2)}, ${track.position.y.toFixed(2)}) km`} />
        <Row k="Error ellipse" v={`${pu.ellipse_major_km.toFixed(2)} x ${pu.ellipse_minor_km.toFixed(2)} km`} />
        <Row k="Orientation" v={`${pu.orientation_deg.toFixed(0)} deg`} />
      </Section>
    </>
  );
  /*
  return (
    <>
      <Section title="POSITION">
        <Row k="Coordinates" v={`(${track.position.x.toFixed(2)}, ${track.position.y.toFixed(2)}) km`} />
        <Row k="Uncertainty" v={`${pu.ellipse_major_km.toFixed(2)} × ${pu.ellipse_minor_km.toFixed(2)} km`} />
        <Row k="Orientation" v={`${pu.orientation_deg.toFixed(0)}°`} />
        {track.rcs_estimate && <Row k="RCS" v={`${track.rcs_estimate.toFixed(1)} m²`} />}
      </Section>

      <Section title="EXISTENCE — P(target is real)">
        <BarRow label="P(real)" value={e} color={COLOR_TRACK} />
        <div style={{ fontSize: 9, color: '#667', marginTop: 2 }}>
          Sensors this cycle: {track.contributing_sensor_ids?.length || 0}
        </div>
      </Section>

      <Section title="TYPE — joint P(type)">
        {sortedTypes.map(([t, p]) => (
          <BarRow key={t} label={t.toUpperCase()} value={p} color={COLOR_TRACK} />
        ))}
        {(1 - sortedTypes.reduce((s, [, p]) => s + p, 0)) > 0.01 && (
          <BarRow label="(unknown / not real)"
                  value={1 - sortedTypes.reduce((s, [, p]) => s + p, 0)}
                  color="#445566" />
        )}
      </Section>

      <Section title="ID — joint P(class)">
        {sortedClasses.slice(0, 6).map(([cls, p]) => (
          <BarRow key={cls} label={cls} value={p} color={COLOR_TRACK} />
        ))}
        <div style={{ fontSize: 9, color: '#667', marginTop: 4 }}>
          Joint = P(real) × P(class | real). On-screen labels show this.
        </div>
      </Section>

      <Section title="ID — conditional P(class | real)">
        {sortedCond.slice(0, 6).map(([cls, p]) => (
          <BarRow key={cls} label={cls} value={p} color="#7faab8" />
        ))}
        <div style={{ fontSize: 9, color: '#667', marginTop: 4 }}>
          What we'd think it is given that something is really there.
        </div>
      </Section>

      <Section title="CONTRIBUTING SENSORS">
        {(track.contributing_sensor_ids || []).map(sid => (
          <div key={sid} style={{ fontSize: 10, color: COLOR_SENSOR, marginBottom: 1 }}>
            {sid}
          </div>
        ))}
      </Section>
    </>
  );
  */
}

function Section({ title, children }) {
  return (
    <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid #15263a' }}>
      <div style={{ fontSize: 9, color: '#5a8aaa', letterSpacing: 1, marginBottom: 4 }}>{title}</div>
      {children}
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#92a8bd', marginBottom: 1 }}>
      <span>{k}</span><span style={{ color: '#cfe6f5' }}>{v}</span>
    </div>
  );
}

function MetricReadout({ label, value }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
      minHeight: 34, marginBottom: 4, gap: 10,
    }}>
      <span style={{
        color: '#92a8bd', fontSize: 10, lineHeight: '12px', flex: 1,
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>{label}</span>
      <span style={{ color: '#cfe6f5', fontSize: 23, lineHeight: '26px', fontWeight: 700 }}>
        {formatPct(value)}
      </span>
    </div>
  );
}

function SensorTrackingReadout({ sensors }) {
  return (
    <div style={{ marginBottom: 4 }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
        fontSize: 10, color: '#92a8bd', marginBottom: 3,
      }}>
        <span>Currently tracking</span>
        <span style={{ color: sensors.length > 0 ? COLOR_SENSOR : '#778' }}>
          {sensors.length > 0 ? `${sensors.length}` : 'none'}
        </span>
      </div>
      <div style={{
        height: 45, display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
        gridAutoRows: 13, gap: 3, overflow: 'hidden',
      }}>
        {sensors.length > 0
          ? sensors.slice(0, 6).map(sid => (
              <div key={sid} style={{
                color: COLOR_SENSOR, fontSize: 10, lineHeight: '13px',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}>
                {sid}
              </div>
            ))
          : <div style={{ color: '#778', fontSize: 10, lineHeight: '13px' }}>none</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Picking

function pickEntity(canvas, state, px, py) {
  const t = computeTransform(canvas);
  // Tracks first (drawn on top)
  for (const tr of state.tracks || []) {
    if (!tr.position) continue;
    if (trackConfidence(tr) < 0.05) continue;
    const [tx, ty] = t.toScreen(tr.position.x, tr.position.y);
    const d = Math.hypot(tx - px, ty - py);
    if (d < 18) return { kind: 'track', id: tr.track_id };
  }
  for (const s of state.sensors || []) {
    const [sx, sy] = t.toScreen(s.position.x, s.position.y);
    if (Math.hypot(sx - px, sy - py) < 14) return { kind: 'sensor', id: s.sensor_id };
  }
  return null;
}

function computeTransform(canvas) {
  const W = canvas.width, H = canvas.height;
  const xRange = MAP_BOUNDS.xMax - MAP_BOUNDS.xMin;
  const yRange = MAP_BOUNDS.yMax - MAP_BOUNDS.yMin;
  const scale = Math.min(W / xRange, H / yRange) * 0.9;
  const offsetX = W / 2 - ((MAP_BOUNDS.xMax + MAP_BOUNDS.xMin) / 2) * scale;
  const offsetY = H / 2 + ((MAP_BOUNDS.yMax + MAP_BOUNDS.yMin) / 2) * scale;
  return {
    toScreen: (x, y) => [offsetX + x * scale, offsetY - y * scale],
    kmToPixels: (km) => km * scale,
    scale,
  };
}

// ---------------------------------------------------------------- Drawing

function drawTacticalDisplay(canvas, state, hover, pinned) {
  // Stash state where the inspect panel can read it
  window.__sensorPipelineState = state;

  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const t = computeTransform(canvas);
  const focusedTrackId = (pinned?.kind === 'track' ? pinned.id : (hover?.kind === 'track' ? hover.id : null));

  ctx.fillStyle = COLOR_BG;
  ctx.fillRect(0, 0, W, H);
  drawGrid(ctx, W, H, t);

  // Sensors
  for (const s of state.sensors || []) {
    drawSensor(ctx, t, s);
  }

  // Tracks
  for (const tr of state.tracks || []) {
    if (!tr.position) continue;
    if (trackConfidence(tr) < 0.05) continue;
    const isFocused = (tr.track_id === focusedTrackId);
    drawTrack(ctx, t, tr, isFocused);
  }
}

function drawGrid(ctx, W, H, t) {
  ctx.strokeStyle = COLOR_GRID;
  ctx.lineWidth = 0.5;
  for (let x = MAP_BOUNDS.xMin; x <= MAP_BOUNDS.xMax; x += 20) {
    const [sx] = t.toScreen(x, 0);
    ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, H); ctx.stroke();
  }
  for (let y = MAP_BOUNDS.yMin; y <= MAP_BOUNDS.yMax; y += 20) {
    const [, sy] = t.toScreen(0, y);
    ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(W, sy); ctx.stroke();
  }
  ctx.fillStyle = COLOR_GRID_LABEL;
  ctx.font = '10px Consolas, Courier New';
  ctx.textAlign = 'center';
  for (let x = MAP_BOUNDS.xMin; x <= MAP_BOUNDS.xMax; x += 40) {
    const [sx, sy] = t.toScreen(x, MAP_BOUNDS.yMin);
    ctx.fillText(`${x}`, sx, sy + 14);
  }
  ctx.textAlign = 'right';
  for (let y = MAP_BOUNDS.yMin; y <= MAP_BOUNDS.yMax; y += 40) {
    const [sx, sy] = t.toScreen(MAP_BOUNDS.xMin, y);
    ctx.fillText(`${y}`, sx - 5, sy + 4);
  }
}

function drawSensor(ctx, t, sensor) {
  const [sx, sy] = t.toScreen(sensor.position.x, sensor.position.y);
  const size = 8;
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = COLOR_SENSOR;
  ctx.fillStyle = COLOR_SENSOR + 'cc';
  ctx.beginPath();
  if (sensor.sensor_type === 'radar') {
    ctx.moveTo(sx, sy - size);
    ctx.lineTo(sx + size, sy + size * 0.7);
    ctx.lineTo(sx - size, sy + size * 0.7);
  } else if (sensor.sensor_type === 'camera') {
    ctx.rect(sx - size, sy - size, size * 2, size * 2);
  } else if (sensor.sensor_type === 'sigint') {
    ctx.moveTo(sx, sy - size);
    ctx.lineTo(sx + size, sy);
    ctx.lineTo(sx, sy + size);
    ctx.lineTo(sx - size, sy);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();

  ctx.fillStyle = COLOR_SENSOR;
  ctx.font = '9px Consolas, Courier New';
  ctx.textAlign = 'center';
  ctx.fillText(sensor.sensor_id, sx, sy + size + 12);
}

function drawTrack(ctx, t, tr, isFocused) {
  const [tx, ty] = t.toScreen(tr.position.x, tr.position.y);

  // Decide displayed class — if leading prob is very low, show as Unknown
  let leadingClass = tr.leading_class;
  if (tr.leading_class_prob < 0.15) leadingClass = 'Unknown';

  // Track confidence drives opacity; certain tracks are bright, tentative ones faded
  const e = trackConfidence(tr);
  const opacity = Math.max(0.25, e);

  // Error ellipse — only when focused (hovered/pinned)
  if (isFocused && tr.position_uncertainty) {
    const SIGMA = 3.0;  // 3-sigma is the standard "uncertainty ellipse"
    const major = Math.max(10, t.kmToPixels(tr.position_uncertainty.ellipse_major_km * SIGMA));
    const minor = Math.max(8, t.kmToPixels(tr.position_uncertainty.ellipse_minor_km * SIGMA));
    const orient = -tr.position_uncertainty.orientation_deg * Math.PI / 180; // canvas y flipped
    ctx.save();
    ctx.translate(tx, ty);
    ctx.rotate(orient);
    ctx.beginPath();
    ctx.ellipse(0, 0, major, minor, 0, 0, Math.PI * 2);
    ctx.fillStyle = COLOR_TRACK + '22';
    ctx.fill();
    ctx.strokeStyle = COLOR_TRACK + 'aa';
    ctx.lineWidth = 1.2;
    ctx.setLineDash([]);
    ctx.stroke();
    ctx.restore();
  }

  // Symbol
  const size = 9 + (isFocused ? 2 : 0);
  drawClassSymbolCanvas(ctx, tx, ty, size, leadingClass, opacity);

  // Compact label: leading_class joint%
  ctx.fillStyle = `rgba(255,179,71,${opacity})`;
  ctx.font = `bold ${isFocused ? 11 : 10}px Consolas, Courier New`;
  ctx.textAlign = 'left';
  const labelX = tx + size + 6;
  ctx.fillText(`${leadingClass}`, labelX, ty - 2);
  ctx.fillStyle = `rgba(180,200,210,${opacity})`;
  ctx.font = `${isFocused ? 9 : 9}px Consolas, Courier New`;
  ctx.fillText(`${(tr.leading_class_prob * 100).toFixed(0)}%`, labelX, ty + 9);
  ctx.fillStyle = `rgba(80,100,120,${opacity})`;
  ctx.font = '8px Consolas, Courier New';
  ctx.fillText(tr.track_id, labelX, ty + 19);
}

function drawClassSymbolCanvas(ctx, x, y, size, klass, opacity) {
  const a = Math.round(opacity * 255).toString(16).padStart(2, '0');
  const stroke = COLOR_TRACK + a;
  const fill = COLOR_TRACK + Math.round(opacity * 0.6 * 255).toString(16).padStart(2, '0');
  ctx.lineWidth = 2;
  ctx.strokeStyle = stroke;
  ctx.fillStyle = fill;
  ctx.beginPath();

  switch (klass) {
    case 'SAM Battery':
      ctx.moveTo(x, y - size);
      ctx.lineTo(x + size, y);
      ctx.lineTo(x, y + size);
      ctx.lineTo(x - size, y);
      ctx.closePath();
      break;
    case 'Radar Station':
      ctx.rect(x - size, y - size, size * 2, size * 2);
      break;
    case 'Command Post':
      for (let i = 0; i < 6; i++) {
        const ang = (i / 6) * Math.PI * 2 + Math.PI / 6;
        const px = x + Math.cos(ang) * size;
        const py = y + Math.sin(ang) * size;
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.closePath();
      break;
    case 'Vehicle Convoy':
      ctx.moveTo(x + size, y);
      ctx.lineTo(x - size * 0.9, y - size);
      ctx.lineTo(x - size * 0.9, y + size);
      ctx.closePath();
      break;
    case 'Infantry':
      ctx.arc(x, y, size * 0.85, 0, Math.PI * 2);
      ctx.closePath();
      break;
    case 'Aircraft':
      ctx.moveTo(x, y - size);
      ctx.lineTo(x + size, y + size * 0.7);
      ctx.lineTo(x - size, y + size * 0.7);
      ctx.closePath();
      break;
    default:
      // Unknown — hollow circle with ?
      ctx.arc(x, y, size, 0, Math.PI * 2);
      ctx.closePath();
      ctx.fillStyle = '#0a1018' + a;
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = stroke;
      ctx.font = `bold ${size + 2}px Consolas, Courier New`;
      ctx.textAlign = 'center';
      ctx.fillText('?', x, y + size * 0.4);
      return;
  }
  ctx.fill();
  ctx.stroke();
}
