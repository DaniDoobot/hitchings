import React, { useEffect, useState, useMemo } from 'react';
import {
  Users,
  UserPlus,
  Shield,
  UserCheck,
  Search,
  Edit2,
  KeyRound,
  X,
  AlertCircle,
  CheckCircle2,
  Lock,
  UserX,
} from 'lucide-react';
import { adminUsersApi } from '../services/adminUsersApi';
import { AdminUser, CreateUserPayload, UpdateUserPayload } from '../types/auth';
import { useAuth } from '../context/AuthContext';

export const AdminUsersPage: React.FC = () => {
  const { user: currentUser } = useAuth();

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Filter state
  const [searchQuery, setSearchQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState<'all' | 'admin' | 'user'>('all');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all');

  // Modals state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [passwordResetUser, setPasswordResetUser] = useState<AdminUser | null>(null);

  // Form states
  const [createForm, setCreateForm] = useState<CreateUserPayload>({
    email: '',
    display_name: '',
    role: 'user',
    password: '',
  });
  const [formSubmitting, setFormSubmitting] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);

  const [editForm, setEditForm] = useState<UpdateUserPayload>({
    email: '',
    display_name: '',
    role: 'user',
    is_active: true,
  });

  const [newPassword, setNewPassword] = useState('');

  const loadUsers = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await adminUsersApi.listUsers();
      setUsers(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error desconocido al cargar usuarios.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, []);

  // Filter users
  const filteredUsers = useMemo(() => {
    return users.filter((u) => {
      const matchesSearch =
        u.email.toLowerCase().includes(searchQuery.toLowerCase()) ||
        u.display_name.toLowerCase().includes(searchQuery.toLowerCase());
      const matchesRole = roleFilter === 'all' || u.role === roleFilter;
      const matchesStatus =
        statusFilter === 'all' ||
        (statusFilter === 'active' && u.is_active) ||
        (statusFilter === 'inactive' && !u.is_active);

      return matchesSearch && matchesRole && matchesStatus;
    });
  }, [users, searchQuery, roleFilter, statusFilter]);

  // Statistics
  const stats = useMemo(() => {
    const total = users.length;
    const admins = users.filter((u) => u.role === 'admin' && u.is_active).length;
    const active = users.filter((u) => u.is_active).length;
    return { total, admins, active };
  }, [users]);

  // Handlers
  const handleOpenCreate = () => {
    setCreateForm({
      email: '',
      display_name: '',
      role: 'user',
      password: '',
    });
    setModalError(null);
    setShowCreateModal(true);
  };

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setModalError(null);

    if (!createForm.email || !createForm.display_name || !createForm.password) {
      setModalError('Todos los campos son obligatorios.');
      return;
    }

    if (createForm.password.length < 12) {
      setModalError('La contraseña debe contener un mínimo de 12 caracteres.');
      return;
    }

    setFormSubmitting(true);
    try {
      await adminUsersApi.createUser(createForm);
      setShowCreateModal(false);
      setSuccessMessage(`Usuario ${createForm.email} creado correctamente.`);
      await loadUsers();
    } catch (err: unknown) {
      setModalError(err instanceof Error ? err.message : 'Error al crear el usuario.');
    } finally {
      setFormSubmitting(false);
    }
  };

  const handleOpenEdit = (target: AdminUser) => {
    setEditingUser(target);
    setEditForm({
      email: target.email,
      display_name: target.display_name,
      role: target.role,
      is_active: target.is_active,
    });
    setModalError(null);
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingUser) return;
    setModalError(null);

    setFormSubmitting(true);
    try {
      await adminUsersApi.updateUser(editingUser.id, editForm);
      setEditingUser(null);
      setSuccessMessage(`Usuario ${editingUser.email} actualizado correctamente.`);
      await loadUsers();
    } catch (err: unknown) {
      setModalError(err instanceof Error ? err.message : 'Error al actualizar el usuario.');
    } finally {
      setFormSubmitting(false);
    }
  };

  const handleOpenResetPassword = (target: AdminUser) => {
    setPasswordResetUser(target);
    setNewPassword('');
    setModalError(null);
  };

  const handleResetPasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!passwordResetUser) return;
    setModalError(null);

    if (newPassword.length < 12) {
      setModalError('La contraseña debe tener al menos 12 caracteres.');
      return;
    }

    setFormSubmitting(true);
    try {
      const res = await adminUsersApi.resetPassword(passwordResetUser.id, { password: newPassword });
      setPasswordResetUser(null);
      setSuccessMessage(res.message || `Contraseña actualizada para ${passwordResetUser.email}.`);
    } catch (err: unknown) {
      setModalError(err instanceof Error ? err.message : 'Error al restablecer la contraseña.');
    } finally {
      setFormSubmitting(false);
    }
  };

  const handleToggleActive = async (target: AdminUser) => {
    const nextState = !target.is_active;
    const actionText = nextState ? 'activar' : 'desactivar';

    if (!window.confirm(`¿Estás seguro de que deseas ${actionText} la cuenta de ${target.email}?`)) {
      return;
    }

    try {
      await adminUsersApi.updateUser(target.id, { is_active: nextState });
      setSuccessMessage(`Cuenta de ${target.email} ${nextState ? 'activada' : 'desactivada'} con éxito.`);
      await loadUsers();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error al modificar el estado de la cuenta.');
    }
  };

  return (
    <div className="space-y-8">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-200 pb-5">
        <div>
          <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-navy-100 text-navy-900 text-xs font-semibold uppercase tracking-wider mb-2">
            <Shield className="w-3.5 h-3.5 text-legal-gold" />
            <span>Administración del Sistema</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-bold font-serif text-slate-900 tracking-tight">
            Gestión de Usuarios
          </h1>
          <p className="text-xs sm:text-sm text-slate-500 mt-1">
            Control de acceso, asignación de roles y administración de cuentas para el Observatorio.
          </p>
        </div>

        <button
          type="button"
          onClick={handleOpenCreate}
          className="inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-navy-900 hover:bg-navy-800 text-white text-xs font-semibold transition-colors shadow-sm self-start sm:self-auto"
        >
          <UserPlus className="w-4 h-4 text-legal-gold" />
          <span>Crear Usuario</span>
        </button>
      </div>

      {/* Alerts */}
      {successMessage && (
        <div className="p-4 rounded-lg bg-emerald-50 border border-emerald-200 flex items-start justify-between gap-3 text-emerald-900 text-xs">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0" />
            <span>{successMessage}</span>
          </div>
          <button
            type="button"
            onClick={() => setSuccessMessage(null)}
            className="text-emerald-700 hover:text-emerald-900"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {error && (
        <div className="p-4 rounded-lg bg-red-50 border border-red-200 flex items-start justify-between gap-3 text-red-900 text-xs">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-600 flex-shrink-0" />
            <span>{error}</span>
          </div>
          <button
            type="button"
            onClick={() => setError(null)}
            className="text-red-700 hover:text-red-900"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* KPI Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl p-5 border border-slate-200 shadow-sm flex items-center gap-4">
          <div className="w-11 h-11 rounded-lg bg-slate-100 flex items-center justify-center text-slate-700">
            <Users className="w-5 h-5" />
          </div>
          <div>
            <span className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
              Total Cuentas
            </span>
            <span className="text-2xl font-bold font-serif text-slate-900">{stats.total}</span>
          </div>
        </div>

        <div className="bg-white rounded-xl p-5 border border-slate-200 shadow-sm flex items-center gap-4">
          <div className="w-11 h-11 rounded-lg bg-amber-50 flex items-center justify-center text-amber-700 border border-amber-200/50">
            <Shield className="w-5 h-5 text-legal-gold" />
          </div>
          <div>
            <span className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
              Administradores Activos
            </span>
            <span className="text-2xl font-bold font-serif text-slate-900">{stats.admins}</span>
          </div>
        </div>

        <div className="bg-white rounded-xl p-5 border border-slate-200 shadow-sm flex items-center gap-4">
          <div className="w-11 h-11 rounded-lg bg-emerald-50 flex items-center justify-center text-emerald-700 border border-emerald-200/50">
            <UserCheck className="w-5 h-5 text-emerald-600" />
          </div>
          <div>
            <span className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
              Usuarios Activos
            </span>
            <span className="text-2xl font-bold font-serif text-slate-900">{stats.active}</span>
          </div>
        </div>
      </div>

      {/* Search & Filter Bar */}
      <div className="bg-white rounded-xl p-4 border border-slate-200 shadow-sm flex flex-col md:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Buscar por correo o nombre..."
            className="w-full pl-9 pr-3 py-2 text-xs rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-legal-gold"
          />
        </div>

        <div className="flex gap-2">
          <select
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value as 'all' | 'admin' | 'user')}
            className="text-xs rounded-lg border border-slate-200 bg-white px-3 py-2 focus:outline-none focus:ring-2 focus:ring-legal-gold text-slate-700"
          >
            <option value="all">Todos los roles</option>
            <option value="admin">Administrador</option>
            <option value="user">Usuario</option>
          </select>

          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as 'all' | 'active' | 'inactive')}
            className="text-xs rounded-lg border border-slate-200 bg-white px-3 py-2 focus:outline-none focus:ring-2 focus:ring-legal-gold text-slate-700"
          >
            <option value="all">Todos los estados</option>
            <option value="active">Activos</option>
            <option value="inactive">Inactivos</option>
          </select>
        </div>
      </div>

      {/* Users Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-12 text-center text-slate-500">
            <div className="w-7 h-7 border-2 border-navy-900 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
            <span className="text-xs">Cargando catálogo de usuarios...</span>
          </div>
        ) : filteredUsers.length === 0 ? (
          <div className="p-12 text-center text-slate-500">
            <Users className="w-8 h-8 mx-auto text-slate-300 mb-2" />
            <p className="text-sm font-semibold text-slate-700">No se encontraron usuarios</p>
            <p className="text-xs text-slate-400 mt-1">Prueba a modificar los filtros de búsqueda.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200 text-[11px] font-semibold text-slate-600 uppercase tracking-wider">
                  <th className="py-3.5 px-4 sm:px-6">Usuario</th>
                  <th className="py-3.5 px-4">Rol</th>
                  <th className="py-3.5 px-4">Estado</th>
                  <th className="py-3.5 px-4 hidden md:table-cell">Fecha de Alta</th>
                  <th className="py-3.5 px-4 hidden lg:table-cell">Último Acceso</th>
                  <th className="py-3.5 px-4 sm:px-6 text-right">Acciones</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-xs">
                {filteredUsers.map((u) => {
                  const isSelf = currentUser?.id === u.id;
                  return (
                    <tr key={u.id} className="hover:bg-slate-50/70 transition-colors">
                      <td className="py-3.5 px-4 sm:px-6">
                        <div className="flex items-center gap-2">
                          <div>
                            <span className="font-semibold text-slate-900 block">
                              {u.display_name}
                            </span>
                            <span className="text-slate-500 text-[11px] block">{u.email}</span>
                          </div>
                          {isSelf && (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-navy-100 text-navy-800 uppercase tracking-wider">
                              Tú
                            </span>
                          )}
                        </div>
                      </td>

                      <td className="py-3.5 px-4">
                        {u.role === 'admin' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-amber-50 text-amber-800 border border-amber-300">
                            <Shield className="w-3 h-3 text-legal-gold" />
                            <span>Administrador</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-700 border border-slate-200">
                            <span>Usuario</span>
                          </span>
                        )}
                      </td>

                      <td className="py-3.5 px-4">
                        {u.is_active ? (
                          <span className="inline-flex items-center gap-1.5 text-emerald-700 font-medium text-[11px]">
                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                            <span>Activo</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 text-slate-400 font-medium text-[11px]">
                            <span className="w-1.5 h-1.5 rounded-full bg-slate-300" />
                            <span>Inactivo</span>
                          </span>
                        )}
                      </td>

                      <td className="py-3.5 px-4 text-slate-500 hidden md:table-cell">
                        {new Date(u.created_at).toLocaleDateString('es-ES', {
                          day: '2-digit',
                          month: 'short',
                          year: 'numeric',
                        })}
                      </td>

                      <td className="py-3.5 px-4 text-slate-500 hidden lg:table-cell">
                        {u.last_login_at
                          ? new Date(u.last_login_at).toLocaleDateString('es-ES', {
                              day: '2-digit',
                              month: 'short',
                              year: 'numeric',
                              hour: '2-digit',
                              minute: '2-digit',
                            })
                          : 'Sin accesos'}
                      </td>

                      <td className="py-3.5 px-4 sm:px-6 text-right">
                        <div className="inline-flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => handleOpenEdit(u)}
                            className="p-1.5 rounded text-slate-400 hover:text-slate-800 hover:bg-slate-100 transition-colors"
                            title="Editar usuario"
                            aria-label={`Editar ${u.email}`}
                          >
                            <Edit2 className="w-4 h-4" />
                          </button>

                          <button
                            type="button"
                            onClick={() => handleOpenResetPassword(u)}
                            className="p-1.5 rounded text-slate-400 hover:text-slate-800 hover:bg-slate-100 transition-colors"
                            title="Resetear contraseña"
                            aria-label={`Resetear contraseña para ${u.email}`}
                          >
                            <KeyRound className="w-4 h-4" />
                          </button>

                          <button
                            type="button"
                            onClick={() => handleToggleActive(u)}
                            className={`p-1.5 rounded transition-colors ${
                              u.is_active
                                ? 'text-slate-400 hover:text-red-600 hover:bg-red-50'
                                : 'text-slate-400 hover:text-emerald-600 hover:bg-emerald-50'
                            }`}
                            title={u.is_active ? 'Desactivar cuenta' : 'Activar cuenta'}
                            aria-label={u.is_active ? `Desactivar ${u.email}` : `Activar ${u.email}`}
                          >
                            {u.is_active ? <UserX className="w-4 h-4" /> : <UserCheck className="w-4 h-4" />}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* MODAL 1: Crear Usuario */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-navy-950/60 backdrop-blur-sm">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-md w-full overflow-hidden">
            <div className="px-6 py-4 bg-navy-950 text-white flex items-center justify-between border-b border-navy-900">
              <div className="flex items-center gap-2">
                <UserPlus className="w-5 h-5 text-legal-gold" />
                <h3 className="text-sm font-semibold font-serif">Crear Nuevo Usuario</h3>
              </div>
              <button
                type="button"
                onClick={() => setShowCreateModal(false)}
                className="text-navy-300 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleCreateSubmit} className="p-6 space-y-4">
              {modalError && (
                <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-red-800 text-xs flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
                  <span>{modalError}</span>
                </div>
              )}

              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                  Nombre Completo
                </label>
                <input
                  type="text"
                  value={createForm.display_name}
                  onChange={(e) => setCreateForm({ ...createForm, display_name: e.target.value })}
                  placeholder="Ej: Daniel González"
                  required
                  className="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-legal-gold"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                  Correo Electrónico
                </label>
                <input
                  type="email"
                  value={createForm.email}
                  onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
                  placeholder="usuario@ejemplo.com"
                  required
                  className="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-legal-gold"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                  Rol de Acceso
                </label>
                <select
                  value={createForm.role}
                  onChange={(e) => setCreateForm({ ...createForm, role: e.target.value as 'admin' | 'user' })}
                  className="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-white focus:outline-none focus:ring-2 focus:ring-legal-gold"
                >
                  <option value="user">Usuario Estándar (solo visualización del observatorio)</option>
                  <option value="admin">Administrador (gestión total de usuarios y sistema)</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                  Contraseña Inicial
                </label>
                <input
                  type="password"
                  value={createForm.password}
                  onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
                  placeholder="Mínimo 12 caracteres"
                  required
                  minLength={12}
                  className="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-legal-gold"
                />
                <span className="text-[10px] text-slate-400 mt-1 block">
                  Debe contener al menos 12 caracteres para cumplir la política de seguridad.
                </span>
              </div>

              <div className="pt-3 flex justify-end gap-2 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="px-3.5 py-2 rounded-lg text-xs font-medium text-slate-600 hover:bg-slate-100"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={formSubmitting}
                  className="px-4 py-2 rounded-lg bg-navy-900 hover:bg-navy-800 text-white text-xs font-semibold disabled:opacity-50"
                >
                  {formSubmitting ? 'Creando...' : 'Crear Usuario'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 2: Editar Usuario */}
      {editingUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-navy-950/60 backdrop-blur-sm">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-md w-full overflow-hidden">
            <div className="px-6 py-4 bg-navy-950 text-white flex items-center justify-between border-b border-navy-900">
              <div className="flex items-center gap-2">
                <Edit2 className="w-5 h-5 text-legal-gold" />
                <h3 className="text-sm font-semibold font-serif">Editar Usuario</h3>
              </div>
              <button
                type="button"
                onClick={() => setEditingUser(null)}
                className="text-navy-300 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleEditSubmit} className="p-6 space-y-4">
              {modalError && (
                <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-red-800 text-xs flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
                  <span>{modalError}</span>
                </div>
              )}

              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                  Nombre Completo
                </label>
                <input
                  type="text"
                  value={editForm.display_name}
                  onChange={(e) => setEditForm({ ...editForm, display_name: e.target.value })}
                  required
                  className="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-legal-gold"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                  Correo Electrónico
                </label>
                <input
                  type="email"
                  value={editForm.email}
                  onChange={(e) => setEditForm({ ...editForm, email: e.target.value })}
                  required
                  className="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-legal-gold"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                  Rol
                </label>
                <select
                  value={editForm.role}
                  onChange={(e) => setEditForm({ ...editForm, role: e.target.value as 'admin' | 'user' })}
                  className="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-white focus:outline-none focus:ring-2 focus:ring-legal-gold"
                >
                  <option value="user">Usuario Estándar</option>
                  <option value="admin">Administrador</option>
                </select>
              </div>

              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="edit_is_active"
                  checked={editForm.is_active}
                  onChange={(e) => setEditForm({ ...editForm, is_active: e.target.checked })}
                  className="rounded border-slate-300 text-navy-900 focus:ring-legal-gold"
                />
                <label htmlFor="edit_is_active" className="text-xs font-medium text-slate-700">
                  Cuenta activa (permite el inicio de sesión)
                </label>
              </div>

              <div className="pt-3 flex justify-end gap-2 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setEditingUser(null)}
                  className="px-3.5 py-2 rounded-lg text-xs font-medium text-slate-600 hover:bg-slate-100"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={formSubmitting}
                  className="px-4 py-2 rounded-lg bg-navy-900 hover:bg-navy-800 text-white text-xs font-semibold disabled:opacity-50"
                >
                  {formSubmitting ? 'Guardando...' : 'Guardar Cambios'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 3: Resetear Contraseña */}
      {passwordResetUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-navy-950/60 backdrop-blur-sm">
          <div className="bg-white rounded-xl shadow-xl border border-slate-200 max-w-md w-full overflow-hidden">
            <div className="px-6 py-4 bg-navy-950 text-white flex items-center justify-between border-b border-navy-900">
              <div className="flex items-center gap-2">
                <Lock className="w-5 h-5 text-legal-gold" />
                <h3 className="text-sm font-semibold font-serif">Restablecer Contraseña</h3>
              </div>
              <button
                type="button"
                onClick={() => setPasswordResetUser(null)}
                className="text-navy-300 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleResetPasswordSubmit} className="p-6 space-y-4">
              <p className="text-xs text-slate-600 leading-relaxed">
                Vas a asignar una nueva contraseña para la cuenta{' '}
                <span className="font-semibold text-slate-900">{passwordResetUser.email}</span>.
              </p>

              <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-amber-900 text-[11px] leading-relaxed">
                <strong>Aviso de seguridad:</strong> Todas las sesiones activas de este usuario serán revocadas de inmediato, exigiéndole identificarse con la nueva clave.
              </div>

              {modalError && (
                <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-red-800 text-xs flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
                  <span>{modalError}</span>
                </div>
              )}

              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
                  Nueva Contraseña
                </label>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="Mínimo 12 caracteres"
                  required
                  minLength={12}
                  className="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-legal-gold"
                />
              </div>

              <div className="pt-3 flex justify-end gap-2 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setPasswordResetUser(null)}
                  className="px-3.5 py-2 rounded-lg text-xs font-medium text-slate-600 hover:bg-slate-100"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={formSubmitting}
                  className="px-4 py-2 rounded-lg bg-navy-900 hover:bg-navy-800 text-white text-xs font-semibold disabled:opacity-50"
                >
                  {formSubmitting ? 'Actualizando...' : 'Actualizar Contraseña'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
