"""Monotonic physical column allocation for sparse and staggered KLE rows."""


def align_matrix_columns(keys,rows):
    """Align rows to the densest row without crossing or sharing row cells.

    Serialized identities, keycodes, physical geometry and row assignments
    stay unchanged. Squared horizontal lead cost is minimized globally for
    each row; a greedy nearest-column choice can strand its later keys.
    """
    groups=[sorted((k for k in keys if k['matrix'][0]==r),key=lambda k:k['center_mm'][0])
            for r in range(rows)]
    reference=max(groups,key=len)
    targets=[round(k['center_mm'][0]*1000000) for k in reference]
    columns=len(targets)
    for group in groups:
        count=len(group)
        cost=[[float('inf')]*(columns+1) for _ in range(count+1)]
        cost[0]=[0]*(columns+1);take={}
        for i,key in enumerate(group,1):
            x=round(key['center_mm'][0]*1000000)
            for j,target in enumerate(targets,1):
                yes=cost[i-1][j-1]+(x-target)**2
                no=cost[i][j-1]
                take[i,j]=yes<no
                cost[i][j]=min(yes,no)
        i,j=count,columns
        while i:
            if take[i,j]:group[i-1]['matrix'][1]=j-1;i-=1
            j-=1
    return columns
