"""Independent single-stock Python API example, consuming saved results only."""
import argparse
import json
from pathlib import Path
from pcs.selection.adapters import load_selection_input
from pcs.selection.models import DecisionPacketInput
from pcs.selection import rank_stock_opportunities,build_decision_evidence_packet
from pcs.selection.storage import write_packet


def main(argv=None, *, loaded=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-manifest',required=True)
    parser.add_argument('--symbol',required=True)
    parser.add_argument('--output-directory',required=True)
    args=parser.parse_args(argv)
    # Callers may reuse an already hash-verified typed batch; standalone CLI reads each package once.
    loaded=loaded or load_selection_input(args.input_manifest)
    inp=loaded.ranking_input
    symbol=args.symbol.upper()
    if symbol not in inp.requested_symbols:
        raise ValueError('NOT_IN_INPUT_SCOPE')
    single=rank_stock_opportunities(inp.model_copy(update={'requested_symbols':[symbol],'previous':None}))
    packet=build_decision_evidence_packet(DecisionPacketInput(symbol=symbol,selection_input=inp,
        extra_records=loaded.extra_records.get(symbol,[])))
    root=write_packet(args.output_directory,packet)
    (root/'single_stock.json').write_text(single.model_dump_json(indent=2),encoding='utf-8')
    report=dict(symbol=symbol,row_id=single.rows[0].row_id,packet_id=packet.packet_id,group=single.rows[0].group,
        batch_scope=inp.requested_symbols,single_scope=single.requested_symbols,
        rank_scope_note='Single-stock rank is 1; row and packet semantics can be compared to the batch.')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return single,packet


if __name__=='__main__':
    main()
