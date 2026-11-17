// k6 load patterns. Select with: k6 run -e PATTERN=spike load/k6-scenarios.js
// PATTERN: steady | ramp | spike | calib     TARGET: base URL (default http://localhost:30080)
// All patterns use arrival-rate executors (open model): requests start at the stated
// rate whether or not earlier ones have finished, so a slow service cannot lower its own load.
import http from 'k6/http';
import { check } from 'k6';

const TARGET = __ENV.TARGET || 'http://localhost:30080';
const WORK_MS = __ENV.WORK_MS || '100';

const patterns = {
  // constant 12 requests/s for 3 minutes
  steady: {
    executor: 'constant-arrival-rate',
    rate: 12, timeUnit: '1s', duration: '3m',
    preAllocatedVUs: 50, maxVUs: 400,
  },
  // gradual growth 2 -> 30 req/s, short hold, then back down (5 minutes)
  ramp: {
    executor: 'ramping-arrival-rate',
    startRate: 2, timeUnit: '1s',
    preAllocatedVUs: 50, maxVUs: 500,
    stages: [
      { target: 30, duration: '2m30s' },
      { target: 30, duration: '30s' },
      { target: 2, duration: '2m' },
    ],
  },
  // sudden flash crowd: low load, jump to 36 req/s in 10 s, hold, then drop (4 minutes)
  spike: {
    executor: 'ramping-arrival-rate',
    startRate: 5, timeUnit: '1s',
    preAllocatedVUs: 50, maxVUs: 600,
    stages: [
      { target: 5, duration: '30s' },
      { target: 36, duration: '10s' },
      { target: 36, duration: '2m' },
      { target: 5, duration: '10s' },
      { target: 5, duration: '1m30s' },
    ],
  },
  // capacity calibration: slow climb against a fixed number of replicas
  calib: {
    executor: 'ramping-arrival-rate',
    startRate: 1, timeUnit: '1s',
    preAllocatedVUs: 50, maxVUs: 600,
    stages: [
      { target: 10, duration: '3m' },
    ],
  },
};

const pattern = __ENV.PATTERN || 'steady';

export const options = {
  // A new connection for every request. kube-proxy balances per connection, so with
  // keep-alive each virtual user stays pinned to the pod it first reached and newly
  // started pods would receive almost no traffic, which hides the effect of scaling.
  // This makes the Service behave like a per-request (layer 7) load balancer.
  noConnectionReuse: true,
  scenarios: { main: patterns[pattern] },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const res = http.get(`${TARGET}/work?ms=${WORK_MS}`, { timeout: '10s' });
  check(res, { 'status 200': (r) => r.status === 200 });
}
