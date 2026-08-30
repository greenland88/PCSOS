from .models import RhythmTransition
def apply_transitions(states, dates, confirmations=2):
    previous={}; pending={}; result=[]
    for date, axes in zip(dates,states):
        for axis, obj in axes.items():
            cur=obj.state; old=previous.get(axis,cur)
            if cur!=old: pending[axis]=(pending.get(axis,(cur,0))[0],pending.get(axis,(cur,0))[1]+1)
            else: pending.pop(axis,None)
            confirmed=cur==old or pending.get(axis,(None,0))[1]>=confirmations
            if confirmed:
                if cur!=old: result.append(RhythmTransition(axis,old,cur,str(date),1,True))
                previous[axis]=cur; pending.pop(axis,None)
    return result
