from datetime import date
from pcs.research.same_ticker_state import SameTickerState

def test_rejected_does_not_contaminate_state():
    s=SameTickerState(); assert s.decide('a',date(2026,6,5))=='OPEN'; assert s.decide('b',date(2026,6,6)).startswith('REJECT'); assert s.ids()==('a',)
def test_admitted_changes_state_and_releases_on_exit():
    s=SameTickerState(); assert s.decide('a',date(2026,6,5))=='OPEN'; s.release(date(2026,6,5)); assert s.count()==1; s.release(date(2026,6,6)); assert s.count()==0
def test_nat_exit_remains_open():
    s=SameTickerState(); s.decide('a',None); s.release(date(2026,12,31)); assert s.count()==1
def test_current_candidate_not_self_counted():
    s=SameTickerState(); assert s.count()==0; assert s.decide('a',date(2026,6,5))=='OPEN'; assert s.count()==1

def test_isolated_split_starts_empty():
    train=SameTickerState(); train.decide('train',date(2026,1,5)); validation=SameTickerState(); assert validation.count()==0

def test_continuous_replay_carries_open_state():
    s=SameTickerState(); s.decide('train',date(2026,6,10)); s.release(date(2026,6,5)); assert s.count()==1; assert s.decide('validation',date(2026,6,11)).startswith('REJECT')

def test_deterministic_candidate_ordering():
    s=SameTickerState(); assert s.decide('a',date(2026,6,5))=='OPEN'; assert s.ids()==('a',)

def test_worker_completion_order_cannot_change_state_result():
    def run(ids):
        s=SameTickerState(); return [s.decide(i,date(2026,6,5)) for i in ids]
    assert run(['a','b']) == run(['a','b'])
