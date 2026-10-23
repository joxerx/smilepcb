"""Routing preferences shared by generated matrix layouts."""
from smilepcb.native_worker import p
from .local_router import LocalRouter


class KeyboardRouter(LocalRouter):
    allow_penalty_fallback=False

    def __init__(self,*args,reserved_shapes=(),**kwargs):
        super().__init__(*args,**kwargs)
        self.reserved_shapes=tuple(reserved_shapes)
        self.routing_penalty=self.matrix_penalty

    @staticmethod
    def matrix_penalty(shape,layer,net,via):
        preferred=p.F_Cu if net.startswith('COL') else p.B_Cu if net.startswith('ROW') else None
        return 2*shape.GetTotalLength() if not via and preferred is not None and layer!=preferred else 0

    def collisions(self,shape,layer,net,via=False):
        if any(p.SHAPE.Collide(shape,reserved,0) for reserved in self.reserved_shapes):
            yield 'reserved-controller-local-wiring'
        yield from super().collisions(shape,layer,net,via)
