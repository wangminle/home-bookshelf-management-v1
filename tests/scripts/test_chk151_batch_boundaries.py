"""CHK-151：清单非法类型、重试成员授权与状态说明。"""
import json
import pytest
from test_opt016_pairing_verify import pair, reload_manifest, by_photo
from test_batch_import_covers import entry, write_manifest, make_covers, run_args, created_resp
import batch_import_covers as bic
import batch_manifest as bm


@pytest.mark.parametrize('bad', [[], {}, True, 9])
def test_invalid_same_as_rejected_cleanly(tmp_path, bad, capsys):
    covers = make_covers(tmp_path, ['a.jpg'])
    path = write_manifest(tmp_path/'m.json', covers, [entry('a.jpg','confirmed',title='测试',photo_id='p0001',same_as=bad)])
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        bm.load_manifest(path)
    assert 'same_as' in capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize('change', ['new_partner','edited_partner','missing_snapshot'])
def test_retry_failed_does_not_authorize_new_recognition(tmp_path, change):
    _, path, fake = pair(tmp_path, root_status='recognized', member_status='recognized',
        behavior={'9787536692930':RuntimeError('[HTTP 400] 失败')})
    m = reload_manifest(path)
    if change == 'new_partner':
        m['entries'][1]['status'] = 'skip'
        fake.behavior = {'三体':RuntimeError('[HTTP 400] 失败')}
        path.write_text(json.dumps(m), encoding='utf-8')
    assert bic.cmd_run(run_args(path,tmp_path/'first.json','--yes','--force'),client=fake) == 1
    m = reload_manifest(path)
    member = m['entries'][1]
    member['status'] = 'recognized'
    if change == 'edited_partner':
        member['title'] = '失败后新识别的标题'
    if change == 'missing_snapshot':
        m['entries'][0]['result'].pop('attempted_members', None)
    path.write_text(json.dumps(m), encoding='utf-8')
    retry = type(fake)(behavior={'9787536692930':created_resp(5)})
    before = path.read_bytes()
    bic.cmd_run(run_args(path,tmp_path/'retry.json','--retry-failed'),client=retry)
    assert retry.calls == []
    assert path.read_bytes() == before
    # 当前人工确认依然可以授权这些照片，不必强迫用户走自动确认。
    m['entries'][1]['status'] = 'confirmed'
    path.write_text(json.dumps(m), encoding='utf-8')
    bic.cmd_run(run_args(path,tmp_path/'confirmed.json','--retry-failed'),client=retry)
    assert len(retry.calls) == 1


def test_status_does_not_call_confirmed_review_flags_an_auto_block(tmp_path, capsys):
    _, path, _ = pair(tmp_path, root_kw={'title':'已人工确认','needs_review':True},member_status='skip')
    bic.cmd_status(bic.build_parser().parse_args(['status','--manifest',str(path)]))
    text = capsys.readouterr().out
    assert '--yes 不会自动入库这些条目' not in text
    assert '人工确认' in text
