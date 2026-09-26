"""Domain-independent numeric evolution and audited model-written parameter blocks."""
import math
import re

BEGIN='// AAD_PARAMETERS_BEGIN'
END='// AAD_PARAMETERS_END'
DECL=re.compile(r'constexpr\s+(double|int)\s+AAD_PARAM_(\d+)_DEFAULT\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*;')


def validate_schema(schema):
    if not isinstance(schema,list) or not 1<=len(schema)<=8:raise ValueError('Expected 1..8 parameter descriptions')
    for item in schema:
        if not isinstance(item,dict):raise ValueError('Parameter description must be an object')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,39}',str(item.get('name',''))):raise ValueError('Invalid parameter name')
        if item.get('kind') not in ('int','float') or item.get('scale') not in ('linear','log'):raise ValueError('Invalid parameter kind/scale')
        values=[item.get(k) for k in ('min','default','max')]
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values):raise ValueError('Parameter values must be finite numbers')
        low,default,high=values
        if not low<=default<=high or low>=high:raise ValueError('Invalid parameter bounds/default')
        if item['scale']=='linear' and not math.isfinite(high-low):raise ValueError('Linear parameter span overflow')
        if item['kind']=='int' and any(int(v)!=v for v in values):raise ValueError('Integer parameter has noninteger bounds/default')
        if item['kind']=='int' and not -2147483648<=low<high<=2147483647:raise ValueError('Integer parameter exceeds C++ int range')
        if item['scale']=='log' and low<=0:raise ValueError('Log parameters require positive bounds')
    if len({p['name'] for p in schema})!=len(schema):raise ValueError('Duplicate parameter names')
    return schema


def parameter_span(code):
    if code.count(BEGIN)!=1 or code.count(END)!=1:raise ValueError('Exactly one parameter block required')
    start=code.index(BEGIN);end=code.index(END)+len(END)
    if end<=start:raise ValueError('Parameter markers reversed')
    return start,end


def validate_block(block,schema,expected=None):
    validate_schema(schema)
    if expected is not None and len(expected)!=len(schema):raise ValueError('Wrong number of requested defaults')
    start,end=parameter_span(block)
    if block[:start].strip() or block[end:].strip():raise ValueError('Code outside parameter block')
    interior=block[start+len(BEGIN):end-len(END)]
    declarations=list(DECL.finditer(interior))
    if DECL.sub('',interior).strip() or len(declarations)!=len(schema):raise ValueError('Only the required constexpr numeric declarations are allowed')
    values=[]
    for i,(declaration,item) in enumerate(zip(declarations,schema)):
        kind,index,literal=declaration.groups();value=float(literal)
        if int(index)!=i or kind!=('int' if item['kind']=='int' else 'double'):raise ValueError('Parameter declaration index/type mismatch')
        if not math.isfinite(value) or not item['min']<=value<=item['max']:raise ValueError('Default outside declared bounds')
        if item['kind']=='int' and value!=int(value):raise ValueError('Noninteger default')
        target=item['default'] if expected is None else float(expected[i])
        if not math.isclose(value,target,rel_tol=1e-12,abs_tol=1e-15):raise ValueError('Default does not match requested value')
        values.append(value)
    return values


def assemble_parameters(parent,block,schema,expected):
    validate_block(block,schema,expected)
    start,end=parameter_span(parent)
    return parent[:start]+block.strip()+parent[end:]


def decode(vector,schema):
    if len(vector)!=len(schema) or any(not math.isfinite(v) for v in vector):raise ValueError('Invalid numeric genome')
    result=[]
    for unit,item in zip(vector,schema):
        unit=min(1.,max(0.,unit));low=item['min'];high=item['max']
        if unit==0:value=low
        elif unit==1:value=high
        else:value=math.exp(math.log(low)+unit*(math.log(high)-math.log(low))) if item['scale']=='log' else low+unit*(high-low)
        value=min(high,max(low,value))
        if item['kind']=='int':result.append(str(round(value)))
        else:result.append(format(value,'.17g'))
    return result


def defaults_vector(schema):
    return [((math.log(p['default'])-math.log(p['min']))/(math.log(p['max'])-math.log(p['min'])) if p['scale']=='log'
             else (p['default']-p['min'])/(p['max']-p['min'])) for p in schema]


def differential_trial(population,target,rng):
    """DE/rand/1/bin in a bounded unit cube; no problem-specific moves."""
    choices=[i for i in range(len(population)) if i!=target]
    a,b,c=rng.sample(choices,3);forced=rng.randrange(len(population[target]))
    return [min(1.,max(0.,population[a][j]+.7*(population[b][j]-population[c][j])))
            if j==forced or rng.random()<.85 else population[target][j]
            for j in range(len(population[target]))]
