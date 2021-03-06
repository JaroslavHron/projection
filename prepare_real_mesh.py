from __future__ import print_function

from dolfin import interpolate, Expression, assemble
from dolfin.cpp.common import mpi_comm_world
from dolfin.cpp.io import HDF5File
from dolfin.cpp.mesh import Mesh, FacetFunction, facets, edges, vertices
from dolfin.functions import FacetNormal, FunctionSpace
from ufl import Measure, dx

import itertools, csv

meshName = 'HYK'

# INPUT DATA ============================
# OLD input data at the end of a file
# reference radius is used to compute reference_coef to multiply inflow velocity to get same volume flow as in reference
# data are stored into [meshName].ini file to be used for parabolic inflow profile generation

# should work for any number of inflows and outflows as longe any two are not in same plane
# planes can intersect rest of the geometry, as is improbable that any exterior facet will have all vertices in given planes
# otherwise it would be necessary to check distance from centerpoint

inflows = [
    {'number': 2, 'normal': [0.0, 1.0, 0.0], 'center': [1.59128, -13.6391, 7.24912], 'radius': 1.01077,
     'reference_radius': 1.01077},
    {'number': 4, 'normal': [0.1, -1.0, -0.37], 'center': [-4.02584, 7.70146, 8.77694], 'radius': 0.553786,
     'reference_radius': 0.553786},
]
outflows = [
    {'number': 3, 'normal': [-0.838444, 0.0, 0.544988], 'center': [11.3086, -0.985461, -5.64479]},
    {'number': 5, 'normal': [-1.0, 0.0, 0.0], 'center': [20.6585, -1.38651, -1.24815]},
]
number_list = [2, 3, 4, 5]  # numbers of inflows, outflows, '1' is reserved for walls
# END OF INPUT ==============================
mesh = Mesh("meshes/" + meshName + ".xml")
tdim = mesh.topology().dim()
mesh.init(tdim-1, tdim)  # needed for facet.exterior()
normal = FacetNormal(mesh)
print("Mesh name: ", meshName, "    ", mesh)
print("Mesh norm max: ", mesh.hmax())
print("Mesh norm min: ", mesh.hmin())
edge_min = 1000
edge_max = 0
for e in edges(mesh):
    l = e.length()
    if l > edge_max:
        edge_max = l
    if l < edge_min:
        edge_min = l
print('edge length max/min:', edge_max, edge_min)
tol = edge_min/10.


# vector product of two lists
def vec(list1, list2):
    return sum(i[0]*i[1] for i in zip(list1, list2))

# vector product of normal and center gives d in ax+by+cz = d equation
for obj in itertools.chain(inflows, outflows):
    obj['d'] = vec(obj['center'], obj['normal'])


# returns number of plane the given point is in
def point_in_subdomain(p):
    for obj in itertools.chain(inflows, outflows):
        if abs(vec(p, obj['normal']) - obj['d']) < tol:
            return obj['number']

# Create boundary markers
facet_function = FacetFunction("size_t", mesh)
for f_mesh in facets(mesh):
    if f_mesh.exterior():
        set = False
        for num in number_list:
            if not set and all(point_in_subdomain(v.point()) == num for v in vertices(f_mesh)):
                facet_function[f_mesh] = num
                set = True
        if not set:
            facet_function[f_mesh] = 1   # wall

f_mesh = HDF5File(mpi_comm_world(), 'meshes/' + meshName + '.hdf5', 'w')
f_mesh.write(mesh, 'mesh')
f_mesh.write(facet_function, 'facet_function')
f_mesh.close()

# compute volume of mesh
V = FunctionSpace(mesh, 'Lagrange', 1)
one = interpolate(Expression('1.'), V)
volume = assemble(one*dx)

# compute real areas of boudary parts
for obj in itertools.chain(inflows, outflows):
    dS = Measure("ds", subdomain_id=obj['number'], subdomain_data=facet_function)
    obj['S'] = assemble(one*dS)

# compute reference coefs
for inf in inflows:
    inf['reference_coef'] = inf['reference_radius']*inf['reference_radius']/(inf['radius']*inf['radius'])

# create .ini file ====================
f_ini = open('meshes/' + meshName + '.ini', 'w')
w = csv.writer(f_ini, delimiter=' ', escapechar='\\', quoting=csv.QUOTE_NONE)
w.writerow(['volume', volume])
for inf in inflows:
    w.writerow(['in', inf['number']])
    w.writerow(['normal'] + inf['normal'])
    w.writerow(['center'] + inf['center'])
    w.writerow(['radius', inf['radius']])
    w.writerow(['reference_coef', inf['reference_coef']])
    w.writerow(['S', inf['S']])
for outf in outflows:
    w.writerow(['out', outf['number']])
    w.writerow(['S', outf['S']])

f_ini.close()

exit()
# OLD INPUT DATA =====================


