import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'

import { useAuth } from '../../auth'

// helper to execute route with admin+ role and do nothing otherwise:
const sudo = (execute) => {
  const auth = useAuth()
  const role = auth.user.role_name
  if (role == 'owner' || role == 'admin') {
    return execute(role)
  } else {
    return null
  }
}

export const useUser = defineStore('app.data.user', () => {
  const name = 'user'
  const key = 'id'

  const data = useData(
    name,
    () =>
      api.http.get(`/users`, {
        use: 'read',
        type: 'load_users'
      }),
    {
      key
    }
  )

  return {
    ...data,
    // api
    read: (id) =>
      api.http.get(`/users/${id}`, {
        use: 'read',
        type: 'read_user'
      }),
    create: (user) =>
      sudo((role) =>
        api.http.post(`/users/${role}/register`, user, {
          use: 'create',
          type: 'register_user'
        })
      ),
    update: ({ id, username, email, role_id }) => {
      const auth = useAuth()
      return id && id !== auth.user.id
        ? sudo((role) =>
            api.http.patch(
              `/users/${role}/${id}`,
              { username, email, role_id },
              {
                use: 'update',
                type: 'update_user'
              }
            )
          )
        : api.http.patch(`/users/me`, { username }, { use: 'update', type: 'update_user' })
    },
    updateMeCreds: ({ currentPassword, newPassword, verifyNewPassword }) =>
      api.http.patch(
        `users/me/creds`,
        {
          current_password: currentPassword,
          new_password: newPassword,
          verify_new_password: verifyNewPassword
        },
        {
          use: 'update',
          type: 'update_password'
        }
      ),
    delete: (user) =>
      sudo((role) =>
        api.http.delete(`/users/${role}/${user.id}`, {
          use: 'delete',
          type: 'delete_user'
        })
      ),
    resetPassword: (user) =>
      sudo((role) =>
        api.http.post(`/users/${role}/${user.id}/reset-password`, null, {
          use: 'read',
          type: 'reset_password'
        })
      ),
    // Owner-only, with no admin equivalent, so it deliberately does not go
    // through sudo() - that would send an admin to a /users/admin/... path
    // which does not exist. The button is gated on the owner role in
    // DialogUserManagement.vue and the route is gated on owner_user.
    requirePasswordChange: () =>
      api.http.post(
        `/users/owner/require-password-change`,
        { confirm: true },
        {
          use: 'update',
          type: 'require_password_change'
        }
      ),
    deleteAccessTokens: (user) =>
      sudo((role) =>
        api.http.delete(`/users/${role}/${user.id}/access-tokens`, {
          use: 'read',
          type: 'delete_access_tokens'
        })
      )
  }
})
