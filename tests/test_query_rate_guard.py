"""Rate/recovery regressions. Only temporary quota files and fake HTTP."""
import ast
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import io
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

CORE = Path(__file__).resolve().parents[1] / 'SOURCE' / 'core'
sys.path.insert(0, str(CORE))
import v7628_query_guard as guard
from v7609_account_guard import allowed_account

URL = 'https://api.kiwoom.com/api/dostk/chart'
HEADERS = {'authorization': 'Bearer fake-token-private', 'api-id': 'ka10080'}


class Clock:
    def __init__(self):
        self.value = 2000000000.0
    def time(self):
        return self.value
    def monotonic(self):
        return self.value
    def sleep(self, seconds):
        self.value += max(0.0, seconds)


class Response:
    def __init__(self, data=None, status=200, headers=None, raw=None):
        self.status = status
        self.headers = headers or {}
        self.content = raw if raw is not None else json.dumps(data or {'return_code': 0, 'ok': True}).encode()
    def read(self):
        return self.content
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


def peak(times):
    return max((sum(0 <= item - start < 1 - 1e-7 for item in times) for start in times), default=0)


def quota_worker(root, credential, queue):
    try:
        budget = guard.AccountQueryBudget('api.kiwoom.com', credential, root=root)
        budget.bind_account('1234031111')
        for _ in range(3):
            budget.wait('ka10080', deadline=time.monotonic()+15)
            queue.put(('ok', time.time()))
    except Exception as exc:
        queue.put(('error', type(exc).__name__))


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.clock = Clock()
    def budget(self, credential='fake-app-key-private', **kwargs):
        return guard.AccountQueryBudget('api.kiwoom.com', credential, root=self.root, clock=self.clock, **kwargs)
    def wait(self, budget, api='ka10080'):
        budget.wait(api, deadline=self.clock.monotonic()+30)
        return self.clock.time()
    def test_single_client_has_four_or_fewer_per_rolling_second(self):
        budget = self.budget()
        self.assertLessEqual(peak([self.wait(budget) for _ in range(30)]), 4)
    def test_same_account_different_credentials_share_quota(self):
        a,b = self.budget('key-a'),self.budget('key-b')
        a.bind_account('1234031111');b.bind_account('1234031122')
        times = [self.wait((a,b)[i%2]) for i in range(20)]
        self.assertLessEqual(peak(times), 4)
    def test_account_binding_preserves_requests_sent_before_verification(self):
        a,b = self.budget('key-a'),self.budget('key-b')
        times=[self.wait(a,'ka00001'),self.wait(b,'ka00001')]
        a.bind_account('1234031111');b.bind_account('1234031111')
        times.extend(self.wait(a) for _ in range(6))
        self.assertLessEqual(peak(times), 4)
    def test_unverified_instances_with_same_credential_share_quota(self):
        a,b=self.budget(),self.budget()
        self.assertLessEqual(peak([self.wait((a,b)[i%2]) for i in range(12)]),4)
    def test_other_account_has_independent_quota(self):
        a,b=self.budget('a'),self.budget('b')
        a.bind_account('1234031111');b.bind_account('9876031111')
        self.assertEqual(self.wait(a),self.wait(b))
    def test_mock_tr_interval_is_at_least_one_second(self):
        b=self.budget(mock=True)
        times=[self.wait(b) for _ in range(6)]
        self.assertTrue(all(y-x>=1.0499 for x,y in zip(times,times[1:])))
    def test_balance_tr_has_extra_margin_under_new_three_per_second_limit(self):
        b=self.budget()
        self.assertLessEqual(peak([self.wait(b,'kt00018') for _ in range(10)]),2)
    def test_account_trs_combined_one_per_second_from_20261112(self):
        b=self.budget()
        apis=['ka00001','kt00018','kt00001','ka10075']*3
        times=[self.wait(b,api) for api in apis]
        self.assertTrue(all(y-x>=1.0499 for x,y in zip(times,times[1:])))
        # 시세 TR은 계좌TR 제한에 묶이지 않는다
        t0=self.wait(b,'kt00018'); t1=self.wait(b,'ka10080')
        self.assertLess(t1-t0,0.5)
    def test_429_cooldown_is_shared(self):
        a,b=self.budget('a'),self.budget('b')
        a.bind_account('1234031111');b.bind_account('1234031111')
        start=self.clock.time();a.penalize(3)
        self.assertGreaterEqual(self.wait(b)-start,3)
    def test_corrupt_file_blocks_instead_of_resetting_quota(self):
        b=self.budget();self.wait(b)
        b.path.write_text('{broken',encoding='utf-8')
        with self.assertRaises(guard.QueryTransportError):self.wait(b)
    def test_no_raw_account_key_or_token_is_written(self):
        b=self.budget();self.wait(b);b.bind_account('1234031111')
        text=b.path.read_text(encoding='utf-8')
        for value in ('fake-app-key-private','1234031111','12340311','fake-token-private'):
            self.assertNotIn(value,text)
    def test_deadline_blocks_unsent_request(self):
        b=self.budget();self.wait(b)
        with self.assertRaises(guard.QueryTransportError):
            b.wait('ka10080',deadline=self.clock.monotonic()+0.01)
    def test_real_processes_share_same_account_quota(self):
        context=multiprocessing.get_context('spawn');queue=context.Queue()
        processes=[context.Process(target=quota_worker,args=(str(self.root),'key-'+str(i),queue)) for i in range(2)]
        for process in processes:process.start()
        results=[queue.get(timeout=25) for _ in range(6)]
        for process in processes:
            process.join(timeout=25)
            self.assertEqual(process.exitcode,0)
        self.assertTrue(all(status=='ok' for status,_ in results),results)
        self.assertLessEqual(peak([stamp for _,stamp in results]),4)
        queue.close();queue.join_thread()


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.clock=Clock();self.calls=[];self.replies=[]
        def open_fake(request,timeout):
            self.calls.append((request,timeout,self.clock.time()))
            result=self.replies.pop(0)
            if callable(result):return result(timeout)
            if isinstance(result,Exception):raise result
            return result
        self.transport=guard.QueryTransport('https://api.kiwoom.com','fake-app-key-private',
            root=self.folder.name,clock=self.clock,opener=open_fake)
    def post(self,headers=None,url=URL):
        return self.transport.post_json(url,{'tic_scope':'1'},headers or HEADERS)
    def test_normal_response_and_pagination_headers_preserved(self):
        self.replies=[Response({'return_code':0,'rows':[1]},headers={'cont-yn':'Y','next-key':'page-two'})]
        data,status,headers=self.post()
        self.assertEqual((data,status,headers),({'return_code':0,'rows':[1]},200,{'cont-yn':'Y','next-key':'page-two'}))
    def test_429_retries_once_and_honors_retry_after(self):
        self.replies=[Response({'return_code':5},429,{'Retry-After':'2'}),Response()]
        self.assertEqual(self.post()[1],200)
        self.assertEqual(len(self.calls),2)
        self.assertGreaterEqual(self.calls[1][2]-self.calls[0][2],2)
    def test_real_urllib_http_error_429_uses_same_recovery(self):
        self.replies=[urllib.error.HTTPError(URL,429,'busy',{'Retry-After':'2'},
                      io.BytesIO(b'{"return_code":5}')),Response()]
        self.assertEqual(self.post()[1],200)
        self.assertEqual(len(self.calls),2)
        self.assertGreaterEqual(self.calls[1][2]-self.calls[0][2],2)
    def test_retry_after_http_date_is_respected(self):
        from email.utils import format_datetime
        stamp=datetime.fromtimestamp(self.clock.time()+3,timezone.utc)
        self.replies=[Response({'return_code':5},429,{'Retry-After':format_datetime(stamp)}),Response()]
        self.assertEqual(self.post()[1],200)
        self.assertGreaterEqual(self.calls[1][2]-self.calls[0][2],3)
    def test_429_twice_is_returned_as_failure(self):
        self.replies=[Response({'return_code':5},429),Response({'return_code':5},429)]
        self.assertEqual(self.post()[1],429);self.assertEqual(len(self.calls),2)
    def test_business_rate_error_is_retried_without_claiming_success(self):
        error={'return_code':5,'return_msg':'1700:허용된 API 요청 개수를 초과'}
        self.replies=[Response(error),Response(error)]
        self.assertEqual(self.post()[0],error);self.assertEqual(len(self.calls),2)
    def test_long_retry_after_does_not_create_long_queue(self):
        self.replies=[Response({'return_code':5},429,{'retry-after':'120'})]
        start=self.clock.time();self.assertEqual(self.post()[1],429)
        self.assertEqual(len(self.calls),1);self.assertLess(self.clock.time()-start,12)
    def test_non_json_429_is_retried(self):
        self.replies=[Response(status=429,raw=b'<html>busy</html>'),Response()]
        self.assertEqual(self.post()[1],200);self.assertEqual(len(self.calls),2)
    def test_timeout_retries_once_with_remaining_budget(self):
        def timeout(seconds):
            self.clock.sleep(seconds);raise urllib.error.URLError(TimeoutError('timed out'))
        self.replies=[timeout,Response()]
        self.assertEqual(self.post()[1],200);self.assertEqual(len(self.calls),2)
        self.assertLessEqual(sum(timeout for _,timeout,_ in self.calls),12)
    def test_repeated_timeout_is_bounded_and_not_success(self):
        def timeout(seconds):
            self.clock.sleep(seconds);raise TimeoutError('timed out')
        self.replies=[timeout,timeout]
        start=self.clock.time()
        with self.assertRaises(guard.QueryTransportError):self.post()
        self.assertEqual(len(self.calls),2);self.assertLessEqual(self.clock.time()-start,12.0001)
    def test_non_timeout_network_failure_has_no_blind_retry(self):
        self.replies=[urllib.error.URLError('DNS failure')]
        with self.assertRaises(guard.QueryTransportError):self.post()
        self.assertEqual(len(self.calls),1)
    def test_token_endpoint_timeout_is_not_reissued(self):
        self.replies=[TimeoutError('timed out')]
        with self.assertRaises(guard.QueryTransportError):
            self.transport.post_json('https://api.kiwoom.com/oauth2/token',{}, {})
        self.assertEqual(len(self.calls),1)
    def test_mock_server_not_paper_label_selects_per_tr_limit(self):
        mock=guard.QueryTransport('https://mockapi.kiwoom.com','key',root=self.folder.name,
                                  clock=self.clock,opener=lambda *args,**kwargs:Response())
        self.assertTrue(mock.budget.mock)
        self.assertFalse(self.transport.budget.mock)
    def test_orders_are_never_sent_or_retried(self):
        for api in ('kt10000','kt10001','kt10002','kt10003'):
            with self.assertRaises(guard.QueryTransportError):self.post({'api-id':api})
        for path in ('ordr','crdordr'):
            with self.assertRaises(guard.QueryTransportError):self.post(url='https://api.kiwoom.com/api/dostk/'+path)
        self.assertEqual(self.calls,[])
    def test_unrelated_business_error_is_not_retried(self):
        self.replies=[Response({'return_code':2,'return_msg':'잘못된 파라미터'})]
        self.assertEqual(self.post()[0]['return_code'],2);self.assertEqual(len(self.calls),1)
    def test_bad_json_is_not_replaced_with_empty_success(self):
        self.replies=[Response(raw=b'bad json')]
        with self.assertRaises(guard.QuerySchemaError):self.post()


# Execute the actual leaf client and original parent authenticate method, but
# replace only setup and HTTP. No production module import or runtime access.
class ApiError(Exception):pass
class AuthenticationError(ApiError):pass
class ApiTransportError(ApiError):pass
class ApiBusinessError(ApiError):pass
class ApiSchemaError(ApiError):pass
errors=SimpleNamespace(V73Error=ApiError,AuthenticationError=AuthenticationError,
    ApiTransportError=ApiTransportError,ApiBusinessError=ApiBusinessError,ApiSchemaError=ApiSchemaError,
    return_code_of=lambda data:int(data['return_code']),append_api_error=Mock())
source=ast.parse((CORE/'bot_v76_00_three_stage_ctrlv_paper.py').read_text(encoding='utf-8-sig'))
client_node=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=='V7609OneMinuteKiwoomClient')
client_node.bases=[ast.Name(id='Base',ctx=ast.Load())]
parent_source=ast.parse((CORE/'bot_v75_3_after_session_guard.py').read_text(encoding='utf-8-sig'))
parent_node=next(n for n in parent_source.body if isinstance(n,ast.ClassDef) and n.name=='KiwoomRestClient')
methods=[n for n in parent_node.body if isinstance(n,ast.FunctionDef) and n.name in {'authenticate','_is_invalid_token_response'}]
class Base:
    def __init__(self):
        self.settings=SimpleNamespace(base_url='https://api.kiwoom.com',timeout_sec=12.,app_key='fake-app-private',
            secret_key='fake-secret-private',assert_credentials=lambda:None)
        self._token='old-fake-token';self._request_lock=threading.RLock()
        self.rank_probe_trials=defaultdict(list);self.logger=Mock()
        self.cache_reads=0
    def _read_cached_token(self):
        self.cache_reads+=1;return 'old-fake-token'
namespace=dict(Base=Base,_V753=errors,_QUERY_GUARD=guard,allowed_account=allowed_account,
    threading=threading,datetime=datetime,timedelta=timedelta,KST=timezone(timedelta(hours=9)),
    _V7609_VERIFIED_ACCOUNT='',AuthenticationError=AuthenticationError,
    return_code_of=errors.return_code_of,TOKEN_FILE=SimpleNamespace(unlink=Mock(side_effect=AssertionError('shared token deletion'))))
for method in methods:
    single=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),method],type_ignores=[])
    exec(compile(ast.fix_missing_locations(single),'<real-parent-auth>', 'exec'),namespace)
    setattr(Base,method.name,namespace[method.name])
exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),client_node],type_ignores=[])),'<real-rest-leaf>', 'exec'),namespace)
Client=namespace['V7609OneMinuteKiwoomClient']


class ClientRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.clock=Clock();self.calls=[];self.responses=[]
        def opener(request,timeout):
            headers={k.lower():v for k,v in request.header_items()}
            api=headers.get('api-id','OAUTH_TOKEN')
            self.calls.append((api,headers,timeout))
            return self.responses.pop(0)
        self.transport=guard.QueryTransport('https://api.kiwoom.com','fake-app-private',
            root=self.folder.name,clock=self.clock,opener=opener)
        self.factory=patch.object(guard,'QueryTransport',return_value=self.transport)
        self.factory.start();self.addCleanup(self.factory.stop)
        self.client=Client()
    def account(self,value='1234031111'):
        return Response({'return_code':0,'acctNo':value})
    def new_token(self):
        return Response({'return_code':0,'token':'new-fake-token','expires_dt':'20990101000000'})
    def invalid(self):
        return Response({'return_code':3,'return_msg':'8005:Token이 유효하지 않습니다'})
    def test_rank_page_refreshes_invalid_token_once_and_keeps_pagination(self):
        self.responses=[self.account(),self.invalid(),self.new_token(),self.account(),
                        Response({'return_code':0,'rows':[1]},headers={'cont-yn':'Y','next-key':'third'})]
        result=self.client._rank_post_page_unlocked('ka10027',{},cont_yn='Y',next_key='second')
        self.assertEqual(result['rows'],[1])
        self.assertEqual([r[0] for r in self.calls],['ka00001','ka10027','OAUTH_TOKEN','ka00001','ka10027'])
        for api,headers,_ in self.calls:
            if api=='ka10027':self.assertEqual((headers['cont-yn'],headers['next-key']),('Y','second'))
        self.assertEqual(self.client._last_response_headers,{'cont-yn':'Y','next-key':'third'})
        self.assertEqual(self.client.cache_reads,0)
    def test_initial_stale_token_is_refreshed_at_account_verification(self):
        self.responses=[self.invalid(),self.new_token(),self.account()]
        self.assertEqual(self.client.authenticate(),'new-fake-token')
        self.assertEqual([r[0] for r in self.calls],['ka00001','OAUTH_TOKEN','ka00001'])
    def test_expiry_is_memory_only_and_skips_stale_shared_cache(self):
        self.client._v7628_token_expiry=datetime.now(namespace['KST'])-timedelta(seconds=1)
        self.responses=[self.new_token(),self.account()]
        self.assertEqual(self.client.authenticate(),'new-fake-token')
        self.assertEqual(self.client.cache_reads,0)
        namespace['TOKEN_FILE'].unlink.assert_not_called()
    def test_wrong_account_after_refresh_blocks_before_requested_query(self):
        self.responses=[self.invalid(),self.new_token(),self.account('1234926611')]
        with self.assertRaises(AuthenticationError):
            self.client.post(api_id='ka10080',endpoint='/api/dostk/chart',body={},required_any=('rows',))
        self.assertNotIn('ka10080',[r[0] for r in self.calls])
        self.assertEqual(namespace['_V7609_VERIFIED_ACCOUNT'],'')
    def test_shared_budget_binding_failure_clears_account_proof(self):
        self.responses=[self.account()]
        with patch.object(self.transport.budget,'bind_account',side_effect=guard.QueryTransportError('blocked')):
            with self.assertRaises(ApiTransportError):self.client.authenticate()
        self.assertEqual(namespace['_V7609_VERIFIED_ACCOUNT'],'')
        self.assertEqual(self.client._v7609_verified_token,'')
    def test_token_only_invalid_setup_fails_without_fake_refresh(self):
        self.client.settings.app_key='';self.client.settings.secret_key=''
        self.responses=[self.invalid()]
        with self.assertRaises(AuthenticationError):self.client.authenticate()
        self.assertEqual(len(self.calls),1)
    def test_repeated_invalid_tokens_stop_after_one_query_refresh(self):
        self.responses=[self.account(),self.invalid(),self.new_token(),self.account(),self.invalid()]
        with self.assertRaises(AuthenticationError):
            self.client.post(api_id='ka10080',endpoint='/api/dostk/chart',body={},required_any=('rows',))
        self.assertEqual(sum(api=='OAUTH_TOKEN' for api,_,_ in self.calls),1)
        self.assertEqual(sum(api=='ka10080' for api,_,_ in self.calls),2)
    def test_concurrent_authentication_validates_token_once(self):
        self.client._token=''
        self.responses=[self.new_token(),self.account()]
        results=[];errors_seen=[]
        def auth():
            try:results.append(self.client.authenticate())
            except Exception as exc:errors_seen.append(exc)
        threads=[threading.Thread(target=auth) for _ in range(8)]
        # No cached token is available in this fixture.
        self.client._read_cached_token=lambda:''
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=10)
        self.assertEqual(errors_seen,[])
        self.assertEqual(len(results),8)
        self.assertEqual([r[0] for r in self.calls],['OAUTH_TOKEN','ka00001'])


if __name__=='__main__':
    unittest.main()
