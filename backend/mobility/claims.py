"""Source-backed rider assertions; summaries are never the sole source of facts.

This conservative English extractor recognizes explicit field declarations and
currency claims. It does not infer facts from assistant text or verify rider claims.
Original case messages remain the lossless audit log. Unparsed corrections require
clarification instead of silently replacing unrelated assertions.
"""
import re

from mobility.corrections import REVISION, clean, nonassertive, resolve, tokens

CORRECTION = re.compile(r'\b(?:correction|corrected|actually|instead|superseded|replace|updated|current)\b', re.I)
DECLARATION = re.compile(
    r'\b(?:my|our)\s+(?P<field>[a-z][a-z -]{0,65}?)\s+(?:is|was|are|were|equals)\s+(?P<value>.+?)(?=[.!?](?:\s|$)|[;\n]|$)', re.I)
LABEL = re.compile(r'(?:^|[.!?]\s+|\n)(?P<field>[a-z][a-z -]{1,55}):\s*(?P<value>.+?)(?=[.!?](?:\s|$)|[;\n]|$)', re.I)
NARRATIVE = re.compile(r"\b(?:the|my|our)\s+(?P<field>[a-z][a-z -]{0,55}?)\s+(?:said|read|showed|says|reads|shows)\s+(?P<value>.+?)(?=[.!?](?:\s|$)|[;\n]|$)", re.I)
MONEY = re.compile(r'\$\d+(?:\.\d{1,2})?')


def field_name(value):
    return ' '.join(w for w in value.lower().split() if w not in {'my','our','the','current','corrected','original','initial','initially','new','previous'})


def assertions(text):
    found = []
    for expression in (DECLARATION, LABEL, NARRATIVE):
        for match in expression.finditer(text):
            field = field_name(match['field'])
            if field in {'correction','note','update'}:
                continue
            value = re.split(r',?\s+and\s+(?:my|our)\s+',match['value'],maxsplit=1,flags=re.I)[0].strip()
            if field and value and not re.match(r"(?:not\b|never\b|wrong\b)",value,re.I):
                found.append((field,clean(value)))
    # Only rider statements in a dispute are claims, never verified billing records.
    if re.search(r'\b(?:disput\w*|refund|amount)\b',text,re.I):
        found.extend(('disputed amount',value) for value in MONEY.findall(text))
    return list(dict.fromkeys(found))


class ClaimLedger:
    def __init__(self, messages):
        self.events=[]
        self.facts=[]
        self.clarification=False
        self.corrections={}
        self.revisions=[]
        self.clarification_reason=None
        for index,message in enumerate(messages):
            if message.get('role') not in ('rider','user'):
                continue
            text=message['content'].replace('’', "'")
            excluded=nonassertive(text)
            self.events.append(dict(source=index,text=text,excluded=excluded))
            if excluded:
                continue
            fields=assertions(text)
            changes,attempted,reason=resolve(text,self.facts,self.events,fields,pending=self.clarification)
            if reason:
                self.clarification=True
                self.clarification_reason=reason
                continue
            for change in changes:
                self.corrections[change.field]=index
                for fact in self.facts:
                    if fact['active'] and fact['field']==change.field and fact['value'].casefold()==change.old.casefold():
                        fact['active']=False
                if not any(f['active'] and f['field']==change.field and f['value'].casefold()==change.new.casefold() for f in self.facts):
                    self.facts.append(dict(field=change.field,value=change.new,source=index,active=True))
                self.revisions.append(dict(field=change.field,old=change.old,new=change.new,source=index,operator=change.operator))
            for field,value in fields:
                if attempted:
                    self.corrections[field]=index
                if any(tokens(field)<=tokens(c.field) or tokens(c.field)<=tokens(field) for c in changes):
                    continue
                if not any(f['active'] and f['field']==field and f['value']==value for f in self.facts):
                    self.facts.append(dict(field=field,value=value,source=index,active=True))
            if changes or (attempted and fields):
                self.clarification=False
                self.clarification_reason=None

    def context(self,query,budget=12000):
        active=[dict(f) for f in self.facts if f['active']]
        inactive={f['value'] for f in self.facts if not f['active']}
        active_values={f['value'] for f in active}
        withdrawn=inactive-active_values
        def redact(text):
            for value in sorted(withdrawn,key=len,reverse=True):
                text=re.sub(r'(?<!\w)'+re.escape(value)+r'(?!\w)','[withdrawn rider claim]',text,flags=re.I)
            return text
        # Rank original rider sentences, not generated replies or an accumulated quote.
        terms=set(re.findall(r'[a-z]+',query.lower()))
        snippets=[]
        seen=set()
        for event in self.events:
            if event.get("excluded"):
                continue
            for sentence in re.split(r'(?<=[.!?])\s+|\n',event['text']):
                # An earlier narrative may name a field without a parseable value.
                # Do not quote it after that field has been explicitly corrected.
                lowered=sentence.lower()
                if any(event['source'] < source and re.search(r'\b'+re.escape(field)+r'\b',lowered)
                       for field,source in self.corrections.items()):
                    continue
                sentence=redact(sentence).strip()
                if sentence and sentence not in seen:
                    seen.add(sentence)
                    overlap=len(terms.intersection(re.findall(r'[a-z]+',sentence.lower())))
                    snippets.append((overlap,event['source'],sentence))
        snippets.sort(key=lambda item:(item[0],item[1]),reverse=True)
        lines=[f"{f['field']}: {redact(f['value'])} [rider message {f['source']}]" for f in active]
        used=sum(len(line)+1 for line in lines)
        overflow=used>budget
        if overflow:
            # Never silently truncate a field and present it as complete.
            lines=[]
        for _,source,sentence in snippets[:8]:
            line=f'{sentence} [rider message {source}]'
            if used+len(line)+1<=budget:
                lines.append(line)
                used+=len(line)+1
        return dict(active_claims=[] if overflow or self.clarification else active,account='' if self.clarification else '\n'.join(lines),requires_clarification=self.clarification or overflow,
                    context_overflow=overflow,source='Original rider messages; not verified trip or payment facts',
                    withdrawn_count=len(withdrawn),clarification_reason=self.clarification_reason,revision_count=len(self.revisions),schema_version=2)
