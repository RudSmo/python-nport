
from __future__ import division

import numpy as np
from scipy import interpolate
import parameter
import twonport


# parameter matrix types
Z = IMPEDANCE = TYPE_Z = 'Z'
Y = ADMITTANCE = TYPE_Y = 'Y'
S = SCATTERING = TYPE_S = 'S'
T = SCATTERING_TRANSFER = TYPE_T = 'T'
H = HYBRID = TYPE_H = 'H'
G = INVERSE_HYBRID = TYPE_G = 'G'
ABCD = TRANSMISSION = TYPE_ABCD = 'ABCD'


#~ class MatrixTypeError(Exception):
    #~ """Inappropriate parameter matrix type"""
    #~ def __init__(self, given, expected):
        #~ self.given = given
        #~ self.expected = expected
    #~ def __str__(self):
        #~ return "given type %s does not match expected type %s" % (self.given, self.expected)



class NPortMatrixBase(np.ndarray):
    """Base class representing an n-port matrix (Z, Y, S, T, G, H or ABCD)"""
    # TODO: implement type checking for operations (as in NPortBase)
    def __new__(cls, matrix, type, z0=None):
        # http://docs.scipy.org/doc/numpy/user/basics.subclassing.html
        obj = np.asarray(matrix, dtype=complex).view(cls)
        if type not in (Z, Y, S, T, H, G, ABCD):
            raise ValueError("illegal matrix type specified")
        obj.type = type
        obj.z0 = obj._z0test(type, z0)
        return obj

    def _z0test(self, type, z0):
        """Verify if a normalizing impedance may be specified for the given
        n-port parameter type
        
        :param type: n-port parameter type
        :type type: Z, Y, S, ABCD or T
        :param z0: normalizing impedance (for S or T)
        :type z0: float
        
        """
        if type in (S, T):
            if z0 is None:
                z0 = 50.0
        elif z0 is not None:
            raise ValueError("the specified n-port representation (%s) does "
                             "not require a reference impedance" % type)
        return z0

    def __array_finalize__(self, obj):
        if obj is None: return
        self.type = getattr(obj, 'type', None)
        self.z0 = getattr(obj, 'z0', None)

    @property
    def ports(self):
        raise NotImplementedError
    
    def convert_z0test(self, type, z0):
        """Check supplied `z0` for conversion and set default value depending on
        the type
        
        :param type: target type for conversion
        :type type: Z, Y, S, T or ABCD
        :param z0: target normalization impedance (for S and T types)
        :type z0: float
        :returns: `z0` or default value when `z0 == None`
        :rtype: float
        """
        if type in (S, T):
            if z0 is None:
                if self.type in (S, T):
                    z0 = self.z0
                else:
                    z0 = 50.0
        elif z0 is not None:
            raise ValueError("the specified n-port representation (%s) does"
                             " not require a reference impedance" % type)
        return z0


class NPortBase(NPortMatrixBase):
    """Base class representing an n-port across a list of frequencies"""
    def __metaclass__(cname, cbases, cdict):
        # http://thread.gmane.org/gmane.comp.python.general/373788/focus=373795
        P = type(cname, cbases, cdict)
        def make_op(name, func):
            def op(this, other):
                if issubclass(type(other), NPortBase):
                    if other.type == this.type:
                        if other.z0 != this.z0:
                            raise ValueError("operands have different Z0")
                        result_type = this.type
                        result_z0 = this.z0
                    else:
                        raise ValueError("operands have different types")
                    min_freq = max(this.freqs[0], other.freqs[0])
                    max_freq = min(this.freqs[-1], other.freqs[-1])
                    result_freqs = list(set(this.freqs).union(set(other.freqs)))
                    result_freqs.sort()
                    result_freqs = \
                        np.array(result_freqs[result_freqs.index(min_freq):
                                              result_freqs.index(max_freq)+1])
                    this_matrices = this.at(result_freqs)
                    other_matrices = other.at(result_freqs)
                    result_matrices = func(this_matrices, other_matrices)
                else:
                    result_freqs = this.freqs
                    result_matrices = func(this, other)
                    result_type = this.type
                    result_z0 = this.z0
                subclass = type(this)
                return subclass(result_freqs, result_matrices, result_type,
                                result_z0)
            op.__name__ = name
            return op
        for name, func in np.ndarray.__dict__.items():
            if callable(func) and name in ['__add__', '__sub__', '__mul__',
                '__div__', '__radd__', '__rsub__', '__rmul__', '__rdiv__']:
                setattr(P, name, make_op(name, func))
        return P

    def __new__(cls, freqs, matrices, type, z0=None):
        if len(freqs) != len(matrices):
            raise ValueError("the list of frequencies and the list of "
                             "matrices should have equal lenghts")
        if matrices[0].shape[0] != matrices[0].shape[1]:
            raise ValueError("the matrices should be square")
        obj = NPortMatrixBase.__new__(cls, matrices, type, z0)
        obj.freqs = np.asarray(freqs)
        return obj

    def __array_finalize__(self, obj):
        NPortMatrixBase.__array_finalize__(self, obj)
        self.freqs = getattr(obj, 'freqs', None)

    def __getitem__(self, arg):
        if type(arg) == int:
            return NPortMatrix(np.asarray(self).__getitem__(arg), self.type,
                               self.z0)
        else:
            return np.asarray(self).__getitem__(arg)

    #~ def __repr__(self):
        #~ return "freqs: " + repr(self.freqs) + "\n" + NPortMatrixBase.__repr__(self)

    #~ def __str__(self):
        #~ return self.__repr__()

    def get_parameter(self, port1, port2):
        """Return the parameter as specified by the indices `port1` and `port2`
        as an ndarray
        
        """
        return np.asarray(self[:, port1 - 1, port2 - 1])

    def get_element(self, port1, port2):
        """Return the submatrices made up of the element as specified by the
        indices `port1` and `port2`
        
        """
        subclass = type(self)
        return subclass(self.freqs,
            np.asarray(self[:, (port1 - 1, ), :][:, :, (port2 - 1, )]),
            self.type, self.z0)

    def at(self, freqs):
        """Return the interpolated n-port data at the given
        * list of frequencies (`freqs` is iterable), or
        * at a single frequency (`freqs` is a value)
        
        """
        # check whether freqs is iterable
        try:
            it = iter(freqs)
            single = False
        except TypeError:
            single = True
            freqs = [freqs]
        subclass = type(self)
        func = interpolate.interp1d(self.freqs, self, axis=0)
        interpolated = func(freqs)
        interpolated_nport = subclass(freqs, interpolated, self.type, self.z0)
        if single:
            return interpolated_nport[0]
        else:
            return interpolated_nport
            
    def average(self, n):
        """Take a moving average over `n` frequency samples"""
        averaged = np.zeros_like(np.asarray(self))
        for i in range(len(self)):
            for j in range(- int(np.floor(n/2)), int(np.ceil(n/2))):
                index = i + j
                if index < 0:
                    index = 0
                elif index >= len(self):
                    index = len(self) - 1
                averaged[i] += self[index] / n        
        # numpy convolve
        #~ ones = np.ones(n) / n
        #~ averaged = np.zeros_like(np.asarray(self))
        #~ for i in range(self.ports):
            #~ for j in range(self.ports):
                #~ averaged[:, i, j] = np.convolve(ones, self[:, i, j])
        # or scipy convolve
        #~ averaged = scipy.signal.convolve(np.ones((n, ) + self.shape[1:])/n, np.asarray(self), 'same')
        subclass = type(self)
        return subclass(self.freqs, averaged, self.type, self.z0)


class NPortMatrix(NPortMatrixBase):
    """Class representing an n-port matrix (Z, Y, S, T, G, H or ABCD)"""
    def __new__(cls, matrix, type, z0=None):
        obj = NPortMatrixBase.__new__(cls, matrix, type, z0)
        if len(obj.shape) != 2:
            raise ValueError("the matrix should be two-dimensional")
        if obj.shape[0] != obj.shape[1]:
            raise ValueError("the matrix should be square")
        return obj

    @property
    def ports(self):
        """The number of ports in this NPortMatrix"""
        return self.shape[0]

    def twonportmatrix(self, inports=None, outports=None):
        """Return the 2n-port matrix represented by this n-port matrix
        
        :param inports: the list of ports that make up the inputs of the 2n-port
        :type inports: tuple or list
        :param outports: the list of ports that make up the outputs
        :type outports: tuple or list
        :rtype: :class:`TwoNPortMatrix`
        
        """
        if self.ports % 2 != 0:
            raise TypeError("this NPortMatrix' number of ports is not a "
                            "multiple of 2")
        n = int(self.ports / 2)
        if inports is None and outports is None:
            inports = range(n)
            outports = range(n, 2*n)
            matrix = self
        else:
            # check whether the given sets of ports are valid
            assert inports is not None and outports is not None
            assert len(inports) == n
            assert len(outports) == n
            allports = set(inports).union(set(outports))
            assert len(allports) == 2*n
            assert min(allports) == 1 and max(allports) == 2*n
            inports = [port - 1 for port in inports]
            outports = [port - 1 for port in outports]

            ports = inports + outports

            # shuffle rows and columns to obtain 2n-port format
            matrix = []
            for row in ports:
                matrix.append(self[row])
            matrix = np.asarray(matrix).T
            matrix2 = []
            for column in ports:
                matrix2.append(matrix[column])
            matrix = np.asarray(matrix2).T

        matrix = np.asmatrix(matrix)
        x11 = matrix[0:n  , 0:n  ]
        x12 = matrix[0:n  , n:2*n]
        x21 = matrix[n:2*n, 0:n  ]
        x22 = matrix[n:2*n, n:2*n]
        matrix = np.array([[x11, x12], [x21, x22]])
        return twonport.TwoNPortMatrix(matrix, self.type, self.z0)

    def renormalize(self, z0):
        """Renormalize the n-port parameters to `z0`"""
        # http://qucs.sourceforge.net/tech/node98.html
        # "Renormalization of S-parameters to different port impedances"
        assert self.type == S
        if z0 == self.z0:
            result = self
        else:
            idty = np.identity(len(self), dtype=complex)

            r = (z0 - self.z0) / (z0 + self.z0)
            result = self.__class__(np.dot(self - idty * r,
                                           np.linalg.inv(idty - r * self)),
                                    self.type, z0)
        return result

    def convert(self, type, z0=None):
        """Convert to another n-port matrix representation"""
        # references:
        #  * http://qucs.sourceforge.net/tech/node98.html
        #           "Transformations of n-Port matrices"
        #  * Multiport S-Parameter Measurements of Linear Circuits With Open Ports
        #           by Reimann et al. (only S <-> Z)
        #  * Agilent AN 154 - S-Parameter Design (S <-> T)
        #  * MATLAB S-parameter toolbox (Z, Y, H, G, ABCD, T)
        #           http://www.mathworks.com/matlabcentral/fileexchange/6080
        z0 = self.convert_z0test(type, z0)
        idty = np.identity(len(self), dtype=complex)
        invert = np.linalg.inv

        if type in (ABCD, T):
            raise TypeError("Cannot convert an NPort to %s-parameter "
                            "representation. Convert to a TwoNPort first" %
                            type)
        elif type not in (Z, Y, S):
            raise TypeError("Unknown n-port parameter type.")

        # TODO: check for singularities
        if self.type == SCATTERING:
            if type == IMPEDANCE:
                result = (2 * invert(idty - self) - idty) * self.z0
            elif type == ADMITTANCE:
                result = (2 * invert(idty + self) - idty) / self.z0
            elif type == SCATTERING:
                if z0 == self.z0:
                    result = self
                else:
                    return self.renormalize(z0)
        elif self.type == IMPEDANCE:
            if type == SCATTERING:
                result = idty - 2 * invert(idty + self / z0)
            elif type == ADMITTANCE:
                result = invert(self)
            elif type == IMPEDANCE:
                result = self
        elif self.type == ADMITTANCE:
            if type == SCATTERING:
                result = 2 * invert(idty + self * z0) - idty
            elif type == IMPEDANCE:
                result = invert(self)
            elif type == ADMITTANCE:
                result = self

        return NPortMatrix(result, type, z0)

    def submatrix(self, ports):
        """Keep only the parameters corresponding to the given ports,
        discarding the others.

        :param ports: list of ports to keep
        :type ports: iterable
        
        """
        indices = [port - 1 for port in ports]
        submatrix = self[indices, :][:, indices]
        return NPortMatrix(submatrix, self.type, self.z0)

    def recombine(self, portsets):
        """Recombine ports, reducing the number of ports of this NPortMatrix.
        This NPortMatrix has to be an impedance matrix.
        
        `portsets` is a list of ints and tuples. An int specifies the number of
        a port that needs to be kept as-is. If the int is negative, the port's
        polarity will be reversed. A tuple specifies a pair of ports that are
        to be recombined into one port. The second element of this tuple acts
        as the ground reference to the first element.
        
        >>> recombine([(1,3), (2,4), 5, -6]
        
        will generate a four-port where:
        
        * port 1 is original port 1 referenced to port 3
        * port 2 is original port 2 referenced to port 4
        * port 3 is original port 5
        * port 4 is original port 6, but with reversed polarity

        """
        assert self.type == IMPEDANCE
        m = []
        for i, ports in enumerate(portsets):
            row = [0 for port in range(self.ports)]
            try:
                if isinstance(ports, tuple):
                    assert len(ports) == 2
                    row[ports[0] - 1] = 1
                    row[ports[1] - 1] = -1
                else:
                    assert isinstance(ports, int)
                    assert ports != 0
                    if ports > 0:
                        row[ports - 1] = 1
                    else:
                        row[-ports - 1] = -1
            except IndexError:
                raise IndexError("specified port number is higher than number "
                                 "of ports")
            m.append(row)
        m = np.matrix(m, dtype=float)
        result = m * np.asarray(self) * m.T
        return NPortMatrix(result, self.type, self.z0)
        
    def ispassive(self):
        """Check whether this n-port matrix is passive"""
        if self.type != SCATTERING:
            return self.convert(SCATTERING).ispassive()
        else:
            if np.max(np.sum(np.abs(np.asarray(self))**2, 1)) > 1:
                return False
            else:
                return True

    def isreciprocal(self):
        """Check whether this n-port matrix is reciprocal"""
        raise NotImplementedError

    def issymmetrical(self):
        """Check whether this n-port matrix is symmetrical"""
        raise NotImplementedError


class NPort(NPortBase):
    """Class representing an n-port across a list of frequencies"""
    def __new__(cls, freqs, matrices, type, z0=None):
        obj = NPortBase.__new__(cls, freqs, matrices, type, z0)
        if len(obj[0].shape) != 2:
            raise ValueError("the matrices should be two-dimensional")
        return obj

    def __init__(self, freqs, matrices, type, z0=50):
        """Initialize an instance, specifying the frequency samples, all
        corresponsing matrices, the matrix type and the reference impedance

        """
        pass

    @property
    def ports(self):
        """The number of ports of this NPort"""
        return self[0].shape[0]

    def add(self, freq, matrix):
        """Return an NPort with the specified frequency sample added

        If matrix is an NPortMatrix, its elements will be converted to this
        NPort's type and characteristic impedance.
        If matrix is a complex array, it is assumed the elements are in the
        format of this NPort

        """
        if type(matrix) == NPortMatrix:
            if matrix.type != self.type or matrix.z0 != self.z0:
                matrix = matrix.convert(self.type, self.z0)
        index = self.freqs.searchsorted(freq)
        freqs = np.insert(self.freqs, index, freq)
        matrices = np.insert(self.matrices, index, matrix, 0)
        return self.__class__(freqs, matrices, self.type, self.z0)

    def twonport(self, inports=None, outports=None):
        """Convert this NPort to a TwoNPort using inports as the input ports
        and outports as the output ports.
        
        :param inports: the list of ports that make up the inputs of the 2n-port
        :type inports: tuple or list
        :param outports: the list of ports that make up the outputs
        :type outports: tuple or list
        :rtype: :class:`TwoNPort`

        """
        twonportmatrices = []
        for matrix in self:
            nportmatrix = NPortMatrix(matrix, self.type, self.z0)
            twonportmatrices.append(nportmatrix.twonportmatrix(inports,
                                                               outports))
        return twonport.TwoNPort(self.freqs, twonportmatrices, self.type,
                                 self.z0)

    def renormalize(self, z0):
        """Renormalize the n-port parameters to `z0`"""
        assert self.type == S
        if z0 == self.z0:
            result = self
        else:
            renormalized = []
            for matrix in self:
                nportmatrix = NPortMatrix(matrix, self.type, self.z0)
                renormalized.append(nportmatrix.renormalize(z0))
            result = self.__class__(self.freqs, renormalized, self.type, z0)
        return result

    def convert(self, type, z0=None):
        """Convert to another n-port matrix representation
        
        :param type: n-port representation type to convert to
        :type type: Z, Y or S
        
        """
        converted = []
        for matrix in self:
            nportmatrix = NPortMatrix(matrix, self.type, self.z0)
            converted.append(nportmatrix.convert(type, z0))
        return NPort(self.freqs, converted, type, z0)

    def submatrix(self, ports):
        """Keep only the parameters corresponding to the given ports,
        discarding the others.
        
        :param ports: list of ports to keep
        :type ports: iterable
        
        """
        indices = [port - 1 for port in ports]
        submatrices = self[:, indices, :][:, :, indices]
        return NPort(self.freqs, submatrices, self.type, self.z0)

    def invert(self):
        """Return an NPort described by the inverse of this NPort's matrices"""
        # TODO: determine type of output
        inverted = []
        for matrix in self:
            nportmatrix = NPortMatrix(matrix, self.type, self.z0)
            inverted.append(np.linalg.inv(nportmatrix))
        return NPort(self.freqs, inverted, self.type, self.z0)

    def recombine(self, portsets):
        """Recombine ports, reducing the number of ports of this NPort.
        
        :param portsets: An int specifies the number of
           a port that needs to be kept as-is. If the int is negative, the
           port's polarity will be reversed. A tuple specifies a pair of ports
           that are to be recombined into one port. The second element of this
           tuple acts as the ground reference to the first element.
        :type portsets: a list of ints and tuples
        
        >>> recombine([(1,3), (2,4), 5, -6]
        
        will generate a four-port where:
        
        * port 1 is original port 1 referenced to port 3
        * port 2 is original port 2 referenced to port 4
        * port 3 is original port 5
        * port 4 is original port 6, but with reversed polarity

        """
        recombined = []
        for i, matrix in enumerate(self):
            nportmatrix = NPortMatrix(matrix, self.type, self.z0)
            recomb = nportmatrix.convert(IMPEDANCE).recombine(portsets)
            recombined.append(recomb.convert(self.type))
        return NPort(self.freqs, recombined, self.type, self.z0)

    def ispassive(self):
        """Check whether this NPort is passive"""
        for nportmatrix in self:
            if not NPortMatrix(nportmatrix, self.type, self.z0).ispassive():
                return False
        return True


def dot(arg1, arg2):
    """Matrix multiplication for NPorts and TwoNPorts"""
    def merge_freqs(freqs1, freqs2):
        minf = max(freqs1[0], freqs2[0])
        maxf = min(freqs1[-1], freqs2[-1])
        result = list(set(freqs1).union(set(freqs2)))
        result.sort()
        return np.array(result[result.index(minf):result.index(maxf)])
    
    if type(arg1) == NPort:
        if type(arg2) == NPort:
            result_freqs = merge_freqs(arg1.freqs, arg2.freqs)
            arg1_matrices = arg1.at(result_freqs)
            arg2_matrices = arg2.at(result_freqs)
            result_matrices = np.asarray([np.dot(a, b) for (a,b) in
                zip(arg1_matrices, arg2_matrices)])
        else:
            result_freqs = arg1.freqs
            result_matrices = np.array([np.dot(matrix, other)
                for matrix in arg1.matrices])
        return NPort(result_freqs, result_matrices, arg1.type, arg1.z0)
    elif type(arg1) == twonport.TwoNPort:
        if type(arg2) == twonport.TwoNPort:
            def twonport_dot(l, r):
                a = np.dot(l[0,0], r[0,0]) + np.dot(l[0,1], r[1,0])
                b = np.dot(l[0,0], r[0,1]) + np.dot(l[0,1], r[1,1])
                c = np.dot(l[1,0], r[0,0]) + np.dot(l[1,1], r[1,0])
                d = np.dot(l[1,0], r[0,1]) + np.dot(l[1,1], r[1,1])
                return np.asarray([[a, b], [c, d]])
            
            result_freqs = merge_freqs(arg1.freqs, arg2.freqs)
            arg1_matrices = arg1.at(result_freqs)
            arg2_matrices = arg2.at(result_freqs)
            result_matrices = np.asarray([twonport_dot(a, b) for (a,b) in
                zip(arg1_matrices, arg2_matrices)])
        else:
            raise NotImplementedError
        return twonport.TwoNPort(result_freqs, result_matrices, arg1.type,
                                 arg1.z0)
    elif type(arg2) == NPort:
        raise NotImplementedError
        
        
    raise NotImplementedError
