"""Shared polygon checks; drafts may be invalid until human acceptance."""

def area(points):
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, points[1:]+points[:1])))/2


def crossing(points):
    if len(points)>1 and points[0]==points[-1]:points=points[:-1]
    def orient(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def on_segment(a,b,p):
        return abs(orient(a,b,p))<=1e-12 and min(a[0],b[0])-1e-12<=p[0]<=max(a[0],b[0])+1e-12 and min(a[1],b[1])-1e-12<=p[1]<=max(a[1],b[1])+1e-12
    n = len(points)
    for i in range(n):
        a, b = points[i], points[(i+1) % n]
        if a==b:return True
        for j in range(i+2, n):
            if (j+1) % n == i:
                continue
            c, d = points[j], points[(j+1) % n]
            if orient(a, b, c)*orient(a, b, d) < 0 and orient(c, d, a)*orient(c, d, b) < 0:
                return True
            if on_segment(a,b,c) or on_segment(a,b,d) or on_segment(c,d,a) or on_segment(c,d,b):return True
    return False


def validate_geometry(annotation):
    """Gate working labels and training export, while preserving raw AI drafts.

    Call after schema validation (coordinates and vertex counts are checked there).
    Empty masks are allowed; a box-only annotation need not invent a contour.
    """
    for zone in annotation['zones']:
        if area(zone['points']) < 1e-5 or crossing(zone['points']):
            raise ValueError(f"Зона {zone['id']}: вырожденный или самопересекающийся полигон")
    for obj in annotation['objects']:
        for keyframe in obj['keyframes']:
            for polygon in keyframe.get('polygons', []):
                if area(polygon) < 1e-8 or crossing(polygon):
                    raise ValueError(f"Объект {obj['id']}, {keyframe['time']} с: вырожденный или самопересекающийся полигон маски")
