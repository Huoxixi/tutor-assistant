"""One visible order per reference, retaining the original stored versions."""
import json
import unicodedata


def reference_key(value):
    return ''.join(unicodedata.normalize('NFKC',value or '').split())


def order_key(order):
    ref=reference_key(order['reference'])
    return ('reference',ref,bool(order['synthetic'])) if ref else ('id',order['id'])


def signature(order):
    # Formatting and evidence spans may vary without changing a requirement.
    return json.dumps({'facts':{k:{'value':v['value'],'mode':v.get('mode','must')} for k,v in order['facts'].items()},
                       'status':order['status'],'issues':sorted(order['validation_issues'])},sort_keys=True,ensure_ascii=False)


def deduplicate_orders(eligible,all_orders):
    versions={};history={}
    for order in all_orders:history.setdefault(order_key(order),[]).append(order)
    for order in eligible:versions.setdefault(order_key(order),[]).append(order)
    results=[];aliases={}
    for key,members in versions.items():
        latest=max(o['_visible_updated_at'] for o in members)
        chosen=max(members,key=lambda o:(o['_visible_updated_at'],o['updated_at'],o['id']))
        combined=dict(chosen)
        combined['match']={k:list(v) if isinstance(v,list) else v for k,v in chosen['match'].items()}
        combined['validation_issues']=list(chosen['validation_issues'])
        combined.pop('_visible_updated_at',None)
        originals=history[key]
        sources=[];seen=set()
        for o in [chosen,*originals]:
            for source in o['sources']:
                source_key=(source['id'],source['text'],source.get('is_update',False))
                if source_key not in seen:sources.append(source);seen.add(source_key)
            aliases[o['id']]=chosen['id']
        combined['sources']=sources
        combined['favorite_at']=min((o['favorite_at'] for o in originals if o['favorite_at']),default=None)
        combined['deduplication']={'version_count':len(originals),'merged_versions':len(originals)-1}
        ambiguous=[o for o in members if o['_visible_updated_at'] in ('',latest)]
        if any(o['status']=='closed' for o in members):
            combined['status']='closed';combined['match']['bucket']='rejected'
            reason='原文明确标明已出或已找到老师'
            if reason not in combined['match']['conflicts']:combined['match']['conflicts'].append(reason)
        elif len({signature(o) for o in ambiguous})>1:
            reason='同单号的最新版本内容不一致，需核对原消息'
            combined['match']['bucket']='pending'
            combined['match']['unknowns'].append(reason)
            combined['validation_issues'].append(reason)
        results.append(combined)
    visible_ids={o['id'] for o in results}
    for order in results:
        order['possible_duplicates']=sorted({aliases[oid] for oid in order['possible_duplicates'] if oid in aliases and aliases[oid]!=order['id'] and aliases[oid] in visible_ids})
    return results
