"""Direction contracts for field trunks, separate from terminal/MCU feeders."""
from dataclasses import dataclass

@dataclass(frozen=True)
class FieldPathPolicy:
    role: str

    def __post_init__(self):
        if self.role not in ('row','column'):
            raise ValueError('Field role must be row or column')

    def accepts_path(self,points):
        for a,b in zip(points,points[1:]):
            dx,dy=b[0]-a[0],b[1]-a[1]
            if not dx and not dy:continue
            if self.role=='row':
                if dy:return False
            elif not dy or dx and abs(dx)!=abs(dy):return False
        return True
