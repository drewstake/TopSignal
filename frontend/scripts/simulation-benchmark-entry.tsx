// Isolated production benchmark entry; never imported by the application.
import { Profiler } from 'react';
import { createRoot } from 'react-dom/client';
import { SimulationPage } from '../src/pages/simulation/SimulationPage';
import { getSimulationRuntime } from '../src/pages/simulation/simulationRuntime';
import '../src/styles/globals.css';
import '../src/styles/workspace.css';
import '../src/styles/signal.css';

const runtime = getSimulationRuntime();
const bench = { runtime, renders: [] as number[] };
Object.assign(window, { bench });
createRoot(document.getElementById('root')!).render(
  <Profiler id="SimulationPage" onRender={(_id, _phase, duration) => bench.renders.push(duration)}>
    <SimulationPage />
  </Profiler>,
);
