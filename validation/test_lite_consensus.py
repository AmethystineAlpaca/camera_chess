from lite_consensus import Consensus
# Allow a real change despite one uncertain frame; don't confirm a brief misread.
g=Consensus('A')
for i,f in enumerate(['B','B','X','B','B','B']):r=g.step(f,i*.4,True,.99)
assert r['confirmed']=='B' and r['changed']
for i in range(3):r=g.step('X',3+i*.4,True,.99)
assert r['confirmed']=='B'
# Low confidence changes never replace a confirmed position.
for i in range(12):r=g.step('C',5+i*.4,True,.80)
assert r['confirmed']=='B'
# Five fast duplicate outputs cannot bypass the time gate.
g=Consensus('A')
for i in range(7):r=g.step('B',i*.05,True,.99)
assert r['confirmed']=='A'
# One weak observation does not flicker the displayed stable status.
g=Consensus('A');g.step('A',0,True);g.step('A',.4,True)
assert g.step('X',.8,False)['phase']=='stable'
print('Consensus transition checks passed')
