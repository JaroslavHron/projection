from __future__ import print_function
from dolfin import assemble, interpolate, Expression, Function, DirichletBC, norm, errornorm, Constant
from dolfin.cpp.common import toc, mpi_comm_world, DOLFIN_EPS
from dolfin.cpp.io import HDF5File
from dolfin.cpp.mesh import Mesh, MeshFunction
from ufl import Measure, dx, cos, sin, FacetNormal, inner, grad, outer, Identity, sym
from math import pi, sqrt

from problems import general_problem as gp
import womersleyBC
print('Imported womersley_cylinder problem.')


class Problem(gp.GeneralProblem):
    def __init__(self, args, tc, metadata):
        self.has_analytic_solution = True
        self.problem_code = 'WCYL'
        super(Problem, self).__init__(args, tc, metadata)

        # TODO check if really used here
        self.tc.init_watch('assembleSol', 'Assembled analytic solution', True)
        self.tc.init_watch('analyticP', 'Analytic pressure', True)
        self.tc.init_watch('analyticVnorms', 'Computed analytic velocity norms', True)
        self.tc.init_watch('errorP', 'Computed pressure error', True)
        self.tc.init_watch('errorV', 'Computed velocity error', True)
        self.tc.init_watch('errorForce', 'Computed force error', True)
        self.tc.init_watch('errorVtest', 'Computed velocity error test', True)
        self.tc.init_watch('computePG', 'Computed pressure gradient', True)

        self.name = 'womersley_cylinder'
        self.status_functional_str = 'last H1 velocity error'

        # input parameters
        self.ic = args.ic
        self.factor = args.factor
        self.metadata['factor'] = self.factor
        self.scale_factor.append(self.factor)

        # fixed parameters (used in analytic solution and in BC)
        self.nu = 3.71 * args.nu # kinematic viscosity
        self.R = 5.0  # cylinder radius

        self.mesh_volume = pi*25.*20.

        # Import gmsh mesh
        self.mesh, self.facet_function = super(Problem, self).loadMesh(args.mesh)
        self.dsIn = Measure("ds", subdomain_id=2, subdomain_data=self.facet_function)
        self.dsOut = Measure("ds", subdomain_id=3, subdomain_data=self.facet_function)
        self.dsWall = Measure("ds", subdomain_id=1, subdomain_data=self.facet_function)
        self.normal = FacetNormal(self.mesh)
        print("Mesh name: ", args.mesh, "    ", self.mesh)
        print("Mesh norm max: ", self.mesh.hmax())
        print("Mesh norm min: ", self.mesh.hmin())

        self.actual_time = None
        self.sol_p = None
        self.last_analytic_pressure_norm = None
        self.v_in = None
        self.area = None

        choose_note = {1.0: '', 0.1: 'nuL10', 0.01: 'nuL100', 10.0: 'nuH10'}
        self.precomputed_filename = args.mesh + choose_note[self.nu_factor]
        print('chosen filename for precomputed solution', self.precomputed_filename)

        # partial Bessel functions and coefficients
        self.bessel_parabolic = None
        self.bessel_real = []
        self.bessel_complex = []
        self.coefs_exp = [-8, -6, -4, -2, 2, 4, 6, 8]

        self.listDict.update({
            'u_H1w': {'list': [], 'name': 'corrected velocity H1 error on wall', 'abrev': 'CE_H1w', 'scale': self.scale_factor,
                     'relative': 'av_norm_H1w', 'slist': []},
            'u2H1w': {'list': [], 'name': 'tentative velocity H1 error on wall', 'abrev': 'TE_H1w', 'scale': self.scale_factor,
                     'relative': 'av_norm_H1w', 'slist': []},
            'av_norm_H1w': {'list': [], 'name': 'analytic velocity H1 norm on wall', 'abrev': 'AVN_H1w'},
            'a_force_wall': {'list': [], 'name': 'analytic force on wall', 'abrev': 'AF'},
            'a_force_wall_normal': {'list': [], 'name': 'analytic force on wall', 'abrev': 'AFN'},
            'a_force_wall_shear': {'list': [], 'name': 'analytic force on wall', 'abrev': 'AFS'},
            'force_wall': {'list': [], 'name': 'force error on wall', 'abrev': 'FE',
                           'relative': 'a_force_wall', 'slist': []},
            'force_wall_normal': {'list': [], 'name': 'normal force error on wall', 'abrev': 'FNE',
                                  'relative': 'a_force_wall', 'slist': []},
            'force_wall_shear': {'list': [], 'name': 'shear force error on wall', 'abrev': 'FSE',
                                 'relative': 'a_force_wall', 'slist': []},
        })

    def __str__(self):
        return 'womersley flow in cylinder'

    @staticmethod
    def setup_parser_options(parser):
        super(Problem, Problem).setup_parser_options(parser)
        # QQ precomputed initial condition?
        # IFNEED smooth initial u0 v_in incompatibility via modification of v_in (options normal, smoothed)
        parser.add_argument('--ic', help='Initial condition', choices=['zero', 'correct'], default='zero')
        parser.add_argument('-F', '--factor', help='Velocity scale factor', type=float, default=1.0)

    def initialize(self, V, Q, PS, D):
        super(Problem, self).initialize(V, Q, PS, D)

        print("IC type: " + self.ic)
        print("Velocity scale factor = %4.2f" % self.factor)
        reynolds = 728.761 * self.factor  # TODO modify by nu_factor
        print("Computing with Re = %f" % reynolds)

        self.v_in = Function(V)
        print('Initializing error control')
        self.load_precomputed_bessel_functions(PS)
        self.solution = self.assemble_solution(0.0)

        # set constants for
        self.area = assemble(interpolate(Expression("1.0"), Q) * self.dsIn)  # inflow area

        # womersley = steady + e^iCt, e^iCt has average 0
        self.pg_normalization_factor.append(womersleyBC.average_analytic_pressure_grad(self.factor))
        self.p_normalization_factor.append(norm(
            interpolate(womersleyBC.average_analytic_pressure_expr(self.factor), self.pSpace), norm_type='L2'))
        self.vel_normalization_factor.append(norm(
            interpolate(womersleyBC.average_analytic_velocity_expr(self.factor), self.vSpace), norm_type='L2'))
        # print('Normalisation factors (vel, p, pg):', self.vel_normalization_factor[0], self.p_normalization_factor[0],
        #       self.pg_normalization_factor[0])

        one = (interpolate(Expression('1.0'), Q))
        self.outflow_area = assemble(one*self.dsOut)
        print('Outflow area:', self.outflow_area)

    def get_boundary_conditions(self, use_pressure_BC, v_space, p_space):
        # boundary parts: 1 walls, 2 inflow, 3 outflow
        # Boundary conditions
        bc0 = DirichletBC(v_space, (0.0, 0.0, 0.0), self.facet_function, 1)
        inflow = DirichletBC(v_space, self.v_in, self.facet_function, 2)
        bcu = [inflow, bc0]
        bcp = []
        if use_pressure_BC:
            outflow = DirichletBC(p_space, 0.0, self.facet_function, 3)
            bcp = [outflow]
        return bcu, bcp

    def get_initial_conditions(self, function_list):
        out = []
        for d in function_list:
            if d['type'] == 'v':
                f = Function(self.vSpace)
                if self.ic == 'correct':
                    f = self.assemble_solution(d['time'])
            if d['type'] == 'p':
                f = Function(self.pSpace)
                if self.ic == 'correct':
                    f = interpolate(womersleyBC.analytic_pressure(self.factor, d['time']), self.pSpace)
            out.append(f)
        return out

    def get_outflow_measures(self):
        return [self.dsOut]

    def get_outflow_measure_form(self):
        return self.dsOut

    def get_v_solution(self, t):
        v = self.assemble_solution(t)
        return v

    def get_p_solution(self, t):
        p = interpolate(womersleyBC.analytic_pressure(self.factor, t), self.pSpace)
        return p

    def update_time(self, actual_time, step_number):
        super(Problem, self).update_time(actual_time, step_number)
        if self.actual_time > 0.5 and int(round(self.actual_time * 1000)) % 1000 == 0:
            self.isWholeSecond = True
            seconds = int(round(self.actual_time))
            self.second_list.append(seconds)
            self.N1 = seconds*self.stepsInSecond
            self.N0 = (seconds-1)*self.stepsInSecond
        else:
            self.isWholeSecond = False

        self.solution = self.assemble_solution(self.actual_time)

        # Update boundary condition
        self.tc.start('updateBC')
        self.v_in.assign(self.solution)
        self.tc.end('updateBC')

        # construct analytic pressure (used for computing pressure and force errors)
        self.tc.start('analyticP')
        analytic_pressure = womersleyBC.analytic_pressure(self.factor, self.actual_time)
        self.sol_p = interpolate(analytic_pressure, self.pSpace)
        self.tc.end('analyticP')

        self.tc.start('analyticVnorms')
        self.analytic_v_norm_L2 = norm(self.solution, norm_type='L2')
        self.analytic_v_norm_H1 = norm(self.solution, norm_type='H1')
        self.analytic_v_norm_H1w = sqrt(assemble((inner(grad(self.solution), grad(self.solution)) +
                                                  inner(self.solution, self.solution)) * self.dsWall))
        self.listDict['av_norm_L2']['list'].append(self.analytic_v_norm_L2)
        self.listDict['av_norm_H1']['list'].append(self.analytic_v_norm_H1)
        self.listDict['av_norm_H1w']['list'].append(self.analytic_v_norm_H1w)
        self.tc.end('analyticVnorms')

    def assemble_solution(self, t):  # returns Womersley sol for time t
        if self.tc is not None:
            self.tc.start('assembleSol')
        sol = Function(self.solutionSpace)
        dofs2 = self.solutionSpace.sub(2).dofmap().dofs()  # gives field of indices corresponding to z axis
        sol.assign(Constant(("0.0", "0.0", "0.0")))  # QQ not needed
        sol.vector()[dofs2] += self.factor * self.bessel_parabolic.vector().array()  # parabolic part of sol
        for idx in range(8):  # add modes of Womersley sol
            sol.vector()[dofs2] += self.factor * cos(self.coefs_exp[idx] * pi * t) * self.bessel_real[idx].vector().array()
            sol.vector()[dofs2] += self.factor * -sin(self.coefs_exp[idx] * pi * t) * self.bessel_complex[idx].vector().array()
        if self.tc is not None:
            self.tc.end('assembleSol')
        return sol

    # load precomputed Bessel functions
    def load_precomputed_bessel_functions(self, PS):
        f = HDF5File(mpi_comm_world(), 'precomputed/precomputed_' + self.precomputed_filename + '.hdf5', 'r')
        temp = toc()
        fce = Function(PS)
        f.read(fce, "parab")
        self.bessel_parabolic = Function(fce)
        for i in range(8):
            f.read(fce, "real%d" % i)
            self.bessel_real.append(Function(fce))
            f.read(fce, "imag%d" % i)
            self.bessel_complex.append(Function(fce))
            # plot(coefs_r_prec[i], title="coefs_r_prec", interactive=True) # reasonable values
            # plot(coefs_i_prec[i], title="coefs_i_prec", interactive=True) # reasonable values
        # plot(c0_prec,title="c0_prec",interactive=True) # reasonable values
        print("Loaded partial solution functions. Time: %f" % (toc() - temp))

    def compute_err(self, is_tent, velocity, t):
        super(Problem, self).compute_err(is_tent, velocity, t)
        er_list_H1w = self.listDict['u2H1w' if is_tent else 'u_H1w']['list']
        errorH1wall = sqrt(assemble((inner(grad(velocity - self.solution), grad(velocity - self.solution)) +
                                     inner(velocity - self.solution, velocity - self.solution)) * self.dsWall))
        er_list_H1w.append(errorH1wall)
        print('  Relative H1wall error:', errorH1wall / self.analytic_v_norm_H1w)
        if self.isWholeSecond:
            self.listDict['u2H1w' if is_tent else 'u_H1w']['slist'].append(
                sqrt(sum([i*i for i in er_list_H1w[self.N0:self.N1]])/self.stepsInSecond))

    def compute_functionals(self, velocity, pressure, t):
        super(Problem, self).compute_functionals(velocity, pressure, t)
        self.compute_force(velocity, pressure, t)

    def compute_force(self, velocity, pressure, t):
        self.tc.start('errorForce')
        I = Identity(3)  # Identity tensor
        def T(p, v):
            return -p * I + 2.0 * self.nu * sym(grad(v))
        error_force = sqrt(
                assemble(inner((T(pressure, velocity) - T(self.sol_p, self.solution)) * self.normal,
                               (T(pressure, velocity) - T(self.sol_p, self.solution)) * self.normal) * self.dsWall))
        an_force = sqrt(assemble(inner(T(self.sol_p, self.solution) * self.normal,
                                            T(self.sol_p, self.solution) * self.normal) * self.dsWall))
        an_f_normal = sqrt(assemble(inner(inner(T(self.sol_p, self.solution) * self.normal, self.normal),
                                               inner(T(self.sol_p, self.solution) * self.normal, self.normal)) * self.dsWall))
        error_f_normal = sqrt(
                assemble(inner(inner((T(self.sol_p, self.solution) - T(pressure, velocity)) * self.normal, self.normal),
                               inner((T(self.sol_p, self.solution) - T(pressure, velocity)) * self.normal, self.normal)) * self.dsWall))
        an_f_shear = sqrt(
                assemble(inner((I - outer(self.normal, self.normal)) * T(self.sol_p, self.solution) * self.normal,
                               (I - outer(self.normal, self.normal)) * T(self.sol_p, self.solution) * self.normal) * self.dsWall))
        error_f_shear = sqrt(
                assemble(inner((I - outer(self.normal, self.normal)) *
                               (T(self.sol_p, self.solution) - T(pressure, velocity)) * self.normal,
                               (I - outer(self.normal, self.normal)) *
                               (T(self.sol_p, self.solution) - T(pressure, velocity)) * self.normal) * self.dsWall))
        self.listDict['a_force_wall']['list'].append(an_force)
        self.listDict['a_force_wall_normal']['list'].append(an_f_normal)
        self.listDict['a_force_wall_shear']['list'].append(an_f_shear)
        self.listDict['force_wall']['list'].append(error_force)
        self.listDict['force_wall_normal']['list'].append(error_f_normal)
        self.listDict['force_wall_shear']['list'].append(error_f_shear)
        if self.isWholeSecond:
            self.listDict['force_wall']['slist'].append(
                sqrt(sum([i*i for i in self.listDict['force_wall']['list'][self.N0:self.N1]])/self.stepsInSecond))
        print('  Relative force error:', error_force/an_force)
        self.tc.end('errorForce')

    def save_pressure(self, is_tent, pressure):
        super(Problem, self).save_pressure(is_tent, pressure)
        self.tc.start('computePG')
        # Report pressure gradient
        p_in = assemble((1.0/self.area) * pressure * self.dsIn)
        p_out = assemble((1.0/self.area) * pressure * self.dsOut)
        computed_gradient = (p_out - p_in)/20.0
        # 20.0 is a length of a pipe NT should depend on mesh length (implement throuhg metadata or function of mesh)
        self.tc.end('computePG')
        self.tc.start('analyticP')
        analytic_gradient = womersleyBC.analytic_pressure_grad(self.factor, self.actual_time)
        if not is_tent:
            self.last_analytic_pressure_norm = norm(self.sol_p, norm_type='L2')
            self.listDict['ap_norm']['list'].append(self.last_analytic_pressure_norm)
        self.tc.end('analyticP')
        self.tc.start('errorP')
        error = errornorm(self.sol_p, pressure, norm_type="l2", degree_rise=0)
        self.listDict['p2' if is_tent else 'p']['list'].append(error)
        print("Normalized pressure error norm:", error/self.p_normalization_factor[0])
        self.listDict['pg2' if is_tent else 'pg']['list'].append(computed_gradient)
        if not is_tent:
            self.listDict['apg']['list'].append(analytic_gradient)
        self.listDict['pgE2' if is_tent else 'pgE']['list'].append(computed_gradient-analytic_gradient)
        self.listDict['pgEA2' if is_tent else 'pgEA']['list'].append(abs(computed_gradient-analytic_gradient))
        if self.isWholeSecond:
            for key in (['pgE2', 'p2'] if is_tent else ['pgE', 'p']):
                self.listDict[key]['slist'].append(
                    sqrt(sum([i*i for i in self.listDict[key]['list'][self.N0:self.N1]])/self.stepsInSecond))
        self.tc.end('errorP')
        if self.doSaveDiff:
            sol_pg_expr = Expression(("0", "0", "pg"), pg=analytic_gradient / self.pg_normalization_factor[0])
            # sol_pg = interpolate(sol_pg_expr, self.pgSpace)
            # plot(sol_p, title="sol")
            # plot(pressure, title="p")
            # plot(pressure - sol_p, interactive=True, title="diff")
            # exit()
            self.pFunction.assign(pressure-self.sol_p)
            self.fileDict['p2D' if is_tent else 'pD']['file'] << self.pFunction
            # self.pgFunction.assign(pg-sol_pg)
            # self.fileDict['pg2D' if is_tent else 'pgD'][0] << self.pgFunction

