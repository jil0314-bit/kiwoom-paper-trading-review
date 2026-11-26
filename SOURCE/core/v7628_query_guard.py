"""Cooperating-process REST query budget. No order submission or token storage."""
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class QueryTransportError(RuntimeError):
    pass


class QuerySchemaError(QueryTransportError):
    pass


class _NonJsonRateLimit(QueryTransportError):
    def __init__(self, headers):
        super().__init__('REST 조회 HTTP=429 응답')
        self.headers = headers


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()

# 2026-11-12(목) 22시~ 키움 공지: 국내주식 계좌TR 19개 유량제어 '초당 1회'.
# 공지 목록 원문을 받지 못해 계좌 분류 TR 전체(19개보다 넓게)를 묶어 합쳐서 1.05초에 1회로 보수 적용한다.
# (이 프로그램이 실제로 쓰는 계좌TR은 ka00001 계좌확인 1건뿐이라 속도 손해 없음)
ACCOUNT_TRS = frozenset({
    'ka00001', 'ka01690', 'ka10072', 'ka10073', 'ka10074', 'ka10075', 'ka10076', 'ka10077',
    'ka10085', 'ka10088', 'ka10170',
    'kt00001', 'kt00002', 'kt00003', 'kt00004', 'kt00005', 'kt00007', 'kt00008', 'kt00009',
    'kt00010', 'kt00011', 'kt00012', 'kt00013', 'kt00015', 'kt00016', 'kt00017', 'kt00018',
    'kt20016', 'kt20017', 'kt50020', 'kt50021', 'kt50030', 'kt50031', 'kt50032', 'kt50075',
})
ACCOUNT_TR_INTERVAL = 1.05


def _digest(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()


class AccountQueryBudget:
    """Four queries/rolling second, shared by users of this protocol.

    Disk contains only scope digests, TR IDs and timing. Unmodified external
    programs do not participate. The first identity uses the app-key digest
    (or token digest for token-only setups); verified accounts bind aliases.
    """
    def __init__(self, host, credential, *, root=None, mock=False, clock=time):
        if not host or not credential:
            raise QueryTransportError('조회 한도 식별정보 미확인')
        self.host = str(host).lower()
        self.origin = _digest('credential|' + self.host + '|' + str(credential))
        self.root = Path(root) if root is not None else Path(tempfile.gettempdir()) / 'kiwoom-rest-query-budget-v1'
        self.mock = bool(mock)
        self.clock = clock
        self.path = self.root / 'budget.json'
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(str(self.root.resolve()), threading.RLock())

    @contextmanager
    def _locked(self):
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / 'budget.lock').open('a+b') as handle:
                handle.seek(0, 2)
                if not handle.tell():
                    handle.write(b'\0')
                    handle.flush()
                deadline = self.clock.monotonic() + 3.0
                while True:
                    try:
                        handle.seek(0)
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if self.clock.monotonic() >= deadline:
                            raise QueryTransportError('계좌 조회 한도 잠금 대기 초과') from None
                        self.clock.sleep(0.01)
                try:
                    if self.path.exists():
                        try:
                            state = json.loads(self.path.read_text(encoding='utf-8'))
                            if (state.get('schema') != 1 or not isinstance(state.get('aliases'), dict)
                                    or not isinstance(state.get('scopes'), dict)):
                                raise ValueError('schema')
                        except (ValueError, TypeError, AttributeError):
                            raise QueryTransportError('공유 조회 한도 기록 오류: 조회 차단') from None
                    else:
                        state = {'schema': 1, 'aliases': {}, 'scopes': {}}
                    yield state
                finally:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_UN)

    def _write(self, state):
        # Atomic replace while the separate lock file is held.
        temporary = self.root / ('budget.' + str(os.getpid()) + '.tmp')
        try:
            temporary.write_text(json.dumps(state, separators=(',', ':')), encoding='utf-8')
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _scope(self, state):
        key = state['aliases'].get(self.origin, self.origin)
        return state['scopes'].setdefault(key, {'events': [], 'blocked_until': 0.0})

    def bind_account(self, account):
        digits = str(account).replace('-', '').strip()
        if not digits.isdigit() or len(digits) not in (8, 10):
            raise QueryTransportError('계좌 조회 한도 연결 근거 미확인')
        target = _digest('account|' + self.host + '|' + digits[:8])
        with self._locked() as state:
            previous_key = state['aliases'].get(self.origin, self.origin)
            if previous_key == target:
                return
            source = state['scopes'].get(previous_key, {'events': [], 'blocked_until': 0.0})
            dest = state['scopes'].setdefault(target, {'events': [], 'blocked_until': 0.0})
            now = self.clock.time()
            dest['events'] = sorted(
                [e for e in dest['events'] + source['events'] if float(e[0]) > now - 1.1],
                key=lambda e: e[0],
            )
            dest['blocked_until'] = max(dest['blocked_until'], source['blocked_until'])
            state['aliases'][self.origin] = target
            self._write(state)

    def wait(self, api_id, *, deadline):
        while True:
            with self._locked() as state:
                scope = self._scope(state)
                now = self.clock.time()
                events = [e for e in scope['events'] if float(e[0]) > now - 1.1]
                allowed = max(now, float(scope['blocked_until']))
                if events:
                    allowed = max(allowed, float(events[-1][0]) + 0.26)
                recent = [e for e in events if float(e[0]) > now - 1.05]
                if len(recent) >= 4:
                    allowed = max(allowed, float(recent[-4][0]) + 1.05)
                same = [e for e in events if e[1] == api_id]
                if same and (self.mock or api_id == 'OAUTH_TOKEN'):
                    allowed = max(allowed, float(same[-1][0]) + 1.05)
                if same and api_id == 'kt00018':
                    allowed = max(allowed, float(same[-1][0]) + 0.51)
                if str(api_id).lower() in ACCOUNT_TRS:
                    account = [e for e in events if str(e[1]).lower() in ACCOUNT_TRS]
                    if account:
                        allowed = max(allowed, float(account[-1][0]) + ACCOUNT_TR_INTERVAL)
                delay = max(0.0, allowed - now)
                if self.clock.monotonic() + delay > deadline:
                    raise QueryTransportError('조회 한도 대기 시간 초과')
                if delay <= 1e-6:
                    events.append([now, str(api_id)])
                    scope['events'] = events
                    self._write(state)
                    return
            self.clock.sleep(delay)

    def penalize(self, delay):
        with self._locked() as state:
            scope = self._scope(state)
            scope['blocked_until'] = max(float(scope['blocked_until']), self.clock.time() + max(0.0, delay))
            self._write(state)


class QueryTransport:
    def __init__(self, base_url, credential, *, timeout=12.0, root=None, clock=time, opener=None, logger=None):
        host = urllib.parse.urlsplit(base_url).hostname or ''
        self.budget = AccountQueryBudget(host, credential, root=root, mock=host.startswith('mockapi.'), clock=clock)
        self.timeout = max(0.1, float(timeout))
        self.clock = clock
        self.opener = opener or urllib.request.urlopen
        self.logger = logger

    def _warn(self, api, reason, attempt):
        if self.logger is not None:
            self.logger.warning('REST_QUERY_RECOVERY api=%s reason=%s attempt=%d', api, reason, attempt)

    def _raw(self, url, body, headers, timeout):
        payload = json.dumps(dict(body), ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(url, data=payload, headers={
            'Content-Type': 'application/json;charset=UTF-8', **dict(headers)}, method='POST')
        try:
            with self.opener(request, timeout=timeout) as response:
                status = int(response.status)
                response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                raw = response.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            response_headers = {str(k).lower(): str(v) for k, v in (exc.headers.items() if exc.headers else [])}
            raw = exc.read().decode('utf-8', errors='replace')
        try:
            data = json.loads(raw)
        except ValueError:
            if status == 429:
                raise _NonJsonRateLimit(response_headers) from None
            raise QuerySchemaError('REST 조회 응답 JSON 형식 오류 HTTP=' + str(status)) from None
        if not isinstance(data, dict):
            raise QuerySchemaError('REST 조회 응답 객체 형식 오류')
        return data, status, response_headers

    @staticmethod
    def _timeout(exc):
        reason = getattr(exc, 'reason', exc)
        return isinstance(reason, (TimeoutError, socket.timeout)) or 'timed out' in str(reason).lower()

    def _retry_after(self, value):
        try:
            delay = float(value)
        except (ValueError, TypeError):
            try:
                delay = parsedate_to_datetime(str(value)).timestamp() - self.clock.time()
            except (ValueError, TypeError, OverflowError):
                delay = 1.0
        return max(1.0, delay) if math.isfinite(delay) else 1.0

    def post_json(self, url, body, headers):
        api = str(headers.get('api-id', '')).strip()
        path = urllib.parse.urlsplit(url).path.rstrip('/').lower()
        if path.endswith(('/ordr', '/crdordr')) or api.lower() in {'kt10000', 'kt10001', 'kt10002', 'kt10003'}:
            raise QueryTransportError('PAPER 통신 계층: 실제 주문 API 차단')
        is_query = bool(api) and path.startswith('/api/dostk/')
        label = api if api else 'OAUTH_TOKEN'
        deadline = self.clock.monotonic() + self.timeout
        last_response = None
        for attempt in range(2 if is_query else 1):
            try:
                self.budget.wait(label, deadline=deadline)
            except QueryTransportError:
                if last_response is not None:
                    return last_response
                raise
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                raise QueryTransportError('REST 조회 전체 대기 시간 초과')
            try:
                result = self._raw(url, body, headers, min(6.0, remaining) if is_query else remaining)
            except _NonJsonRateLimit as exc:
                if is_query:
                    self.budget.penalize(self._retry_after(exc.headers.get('retry-after')))
                    if attempt == 0 and self.clock.monotonic() + 1.0 < deadline:
                        self._warn(api, 'rate_limit_retry', attempt + 1)
                        continue
                raise QueryTransportError('REST 조회 HTTP=429 재시도 한도/시간 초과') from None
            except (OSError, urllib.error.URLError) as exc:
                if is_query and attempt == 0 and self._timeout(exc) and self.clock.monotonic() + 0.3 < deadline:
                    self._warn(api, 'timeout_retry', attempt + 1)
                    self.clock.sleep(0.25)
                    continue
                label_error = '타임아웃' if self._timeout(exc) else '네트워크 실패'
                raise QueryTransportError('REST ' + label_error + ' api=' + label) from None
            data, status, response_headers = result
            last_response = result
            message = str(data.get('return_msg', ''))
            throttled = status == 429 or (str(data.get('return_code', '0')) != '0' and (
                '1700:' in message or '허용된 API 요청 개수를 초과' in message))
            if not throttled or not is_query:
                return result
            self.budget.penalize(self._retry_after(response_headers.get('retry-after')))
            if attempt == 0:
                self._warn(api, 'rate_limit_retry', attempt + 1)
        return last_response
